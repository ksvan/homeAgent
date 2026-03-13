from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, AsyncGenerator

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, StreamingResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])

# Shutdown signal: set this before stopping the server so open SSE connections
# exit cleanly and uvicorn can drain without being force-cancelled.
_stream_shutdown = asyncio.Event()


def signal_stream_shutdown() -> None:
    """Signal all active admin SSE streams to close gracefully."""
    _stream_shutdown.set()


@router.get("", response_class=HTMLResponse)
async def admin_page() -> str:
    return _ADMIN_HTML


@router.get("/stats")
async def admin_stats() -> dict[str, Any]:
    import psutil

    from app.db import cache_session
    from app.models.cache import AgentRunLog
    from sqlmodel import select

    proc = psutil.Process()
    mem = proc.memory_info()
    # psutil.boot_time() is system uptime; use process create time for app uptime
    app_uptime_s = int(time.time() - proc.create_time())

    with cache_session() as session:
        runs = session.exec(
            select(AgentRunLog).order_by(AgentRunLog.created_at.desc()).limit(500)
        ).all()

    tool_counts: dict[str, int] = {}
    total_input_tokens = 0
    total_output_tokens = 0
    model_counts: dict[str, int] = {}
    error_count = 0
    durations: list[int] = []

    for run in runs:
        try:
            tools = json.loads(run.tools_called)
            for t in tools:
                name = t.get("tool", "unknown")
                tool_counts[name] = tool_counts.get(name, 0) + 1
        except Exception:
            pass
        try:
            tokens = json.loads(run.tokens_used)
            total_input_tokens += tokens.get("input", 0)
            total_output_tokens += tokens.get("output", 0)
        except Exception:
            pass
        model_counts[run.model_used] = model_counts.get(run.model_used, 0) + 1
        if run.duration_ms:
            durations.append(run.duration_ms)

    avg_duration = int(sum(durations) / len(durations)) if durations else 0

    # Memory stats from memory.db
    from app.db import memory_session
    from app.models.memory import ConversationMessage, ConversationSummary, EpisodicMemory

    with memory_session() as msession:
        episodic_total = len(msession.exec(select(EpisodicMemory.id)).all())
        episodic_auto = len(
            msession.exec(
                select(EpisodicMemory.id).where(EpisodicMemory.source_run_id.isnot(None))
            ).all()
        )
        messages_total = len(msession.exec(select(ConversationMessage.id)).all())
        summaries_total = len(msession.exec(select(ConversationSummary.id)).all())

    return {
        "system": {
            "cpu_percent": round(proc.cpu_percent(interval=0.1), 1),
            "memory_mb": round(mem.rss / 1024 / 1024, 1),
            "uptime_seconds": app_uptime_s,
        },
        "runs": {
            "total": len(runs),
            "avg_duration_ms": avg_duration,
        },
        "tools": tool_counts,
        "tokens": {
            "input": total_input_tokens,
            "output": total_output_tokens,
        },
        "models": model_counts,
        "memory": {
            "episodic_total": episodic_total,
            "episodic_auto": episodic_auto,
            "episodic_manual": episodic_total - episodic_auto,
            "messages_total": messages_total,
            "summaries_total": summaries_total,
        },
    }


@router.get("/stream")
async def admin_stream() -> StreamingResponse:
    from app.control.events import get_recent_events, subscribe, unsubscribe

    async def event_generator() -> AsyncGenerator[str, None]:
        q = subscribe()
        try:
            # Replay recent history to new client
            for event in get_recent_events():
                yield _format_sse(event)
            # Then stream new events; poll in short bursts so shutdown is noticed quickly
            heartbeat_deadline = asyncio.get_event_loop().time() + 30.0
            while not _stream_shutdown.is_set():
                try:
                    event = await asyncio.wait_for(q.get(), timeout=1.0)
                    yield _format_sse(event)
                    heartbeat_deadline = asyncio.get_event_loop().time() + 30.0
                except asyncio.TimeoutError:
                    if asyncio.get_event_loop().time() >= heartbeat_deadline:
                        yield ": heartbeat\n\n"
                        heartbeat_deadline = asyncio.get_event_loop().time() + 30.0
        except asyncio.CancelledError:
            pass
        finally:
            unsubscribe(q)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _format_sse(event: object) -> str:
    from app.control.events import ControlEvent

    assert isinstance(event, ControlEvent)
    data = json.dumps({"run_id": event.run_id, "ts": event.ts, **event.payload})
    return f"event: {event.event_type}\ndata: {data}\n\n"


@router.get("/memory")
async def admin_memory() -> dict[str, Any]:
    from sqlmodel import select

    from app.db import memory_session, users_session
    from app.models.memory import (
        ConversationSummary,
        EpisodicMemory,
        HouseholdProfile,
        UserProfile,
    )
    from app.models.users import User

    with memory_session() as ms:
        memories = ms.exec(
            select(EpisodicMemory).order_by(EpisodicMemory.created_at.desc())
        ).all()
        u_profiles = ms.exec(select(UserProfile)).all()
        h_profiles = ms.exec(select(HouseholdProfile)).all()
        summaries = ms.exec(select(ConversationSummary)).all()

    # Resolve user IDs → display names via users.db
    user_ids: set[str] = {m.user_id for m in memories if m.user_id}
    user_ids |= {p.user_id for p in u_profiles}
    user_ids |= {s.user_id for s in summaries}

    user_names: dict[str, str] = {}
    if user_ids:
        with users_session() as us:
            rows = us.exec(select(User).where(User.id.in_(list(user_ids)))).all()
            user_names = {u.id: u.name for u in rows}

    h0 = h_profiles[0] if h_profiles else None

    return {
        "episodic": [
            {
                "id": m.id,
                "content": m.content,
                "scope": (
                    user_names.get(m.user_id, m.user_id[:8]) if m.user_id else "household"
                ),
                "importance": m.importance,
                "created_at": m.created_at.isoformat() if m.created_at else None,
                "last_used_at": m.last_used_at.isoformat() if m.last_used_at else None,
            }
            for m in memories
        ],
        "user_profiles": [
            {
                "user": user_names.get(p.user_id, p.user_id[:8]),
                "data": json.loads(p.summary) if p.summary else {},
                "updated_at": p.updated_at.isoformat() if p.updated_at else None,
            }
            for p in u_profiles
        ],
        "household_profile": {
            "data": json.loads(h0.summary) if h0 and h0.summary else {},
            "updated_at": h0.updated_at.isoformat() if h0 and h0.updated_at else None,
        },
        "summaries": [
            {
                "user": user_names.get(s.user_id, s.user_id[:8]),
                "text": s.summary,
                "created_at": s.created_at.isoformat() if s.created_at else None,
            }
            for s in summaries
        ],
    }


@router.get("/scheduler")
async def admin_scheduler() -> dict[str, Any]:
    import json as _json

    from sqlmodel import select

    from app.db import users_session
    from app.models.scheduled_prompts import ScheduledPrompt
    from app.models.tasks import Task
    from app.scheduler.scheduled_prompts import recurrence_label

    with users_session() as session:
        tasks = session.exec(
            select(Task).where(Task.status == "ACTIVE").order_by(Task.created_at)
        ).all()
        sps = session.exec(
            select(ScheduledPrompt)
            .where(ScheduledPrompt.enabled == True)  # noqa: E712
            .order_by(ScheduledPrompt.created_at)
        ).all()

    reminders = []
    actions = []
    for t in tasks:
        ctx = _json.loads(t.context)
        if "reminder_text" in ctx:
            reminders.append({
                "id": t.id,
                "user_id": t.user_id,
                "text": ctx.get("reminder_text", ""),
                "scheduled_at": ctx.get("scheduled_at", ""),
            })
        elif "action_tool" in ctx:
            actions.append({
                "id": t.id,
                "user_id": t.user_id,
                "description": ctx.get("action_description", t.title),
                "tool": ctx.get("action_tool", ""),
                "scheduled_at": ctx.get("scheduled_at", ""),
            })

    scheduled_prompts = [
        {
            "id": sp.id,
            "user_id": sp.user_id,
            "name": sp.name,
            "recurrence": recurrence_label(sp.recurrence, sp.time_of_day),
            "prompt": sp.prompt[:120] + ("…" if len(sp.prompt) > 120 else ""),
        }
        for sp in sps
    ]

    return {"reminders": reminders, "actions": actions, "scheduled_prompts": scheduled_prompts}


# ---------------------------------------------------------------------------
# Embedded admin UI
# ---------------------------------------------------------------------------

_ADMIN_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>HomeAgent — Admin</title>
<style>
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
:root {
  --bg: #0d1117; --surface: #161b22; --border: #30363d;
  --text: #c9d1d9; --dim: #adbac7; --accent: #7c5cfc;
  --green: #3fb950; --blue: #58a6ff; --red: #f85149; --yellow: #d29922;
}
body { background: var(--bg); color: var(--text); font-family: ui-monospace, 'SF Mono', Menlo, monospace; font-size: 13px; line-height: 1.5; height: 100vh; display: flex; flex-direction: column; overflow: hidden; }

/* Header */
header { background: var(--surface); border-bottom: 1px solid var(--border); padding: 10px 20px; display: flex; align-items: center; gap: 12px; flex-shrink: 0; }
.logo { color: var(--accent); font-weight: 700; font-size: 14px; letter-spacing: 0.04em; }
.pulse { width: 7px; height: 7px; border-radius: 50%; background: var(--green); box-shadow: 0 0 0 0 rgba(63,185,80,.4); animation: pulse 2s infinite; }
.pulse.off { background: var(--red); box-shadow: none; animation: none; }
@keyframes pulse { 0%,100%{box-shadow:0 0 0 0 rgba(63,185,80,.4)} 50%{box-shadow:0 0 0 5px rgba(63,185,80,0)} }
#conn-label { font-size: 11px; color: var(--dim); }
#conn-label.ok { color: var(--green); }
#conn-label.err { color: var(--red); }
.hdr-right { margin-left: auto; display: flex; gap: 20px; align-items: center; }
.hdr-stat { font-size: 11px; color: var(--dim); }
.hdr-stat span { color: var(--text); }

/* Layout */
.layout { display: grid; grid-template-columns: 300px 1fr; flex: 1; min-height: 0; gap: 1px; background: var(--border); }

/* Sidebar */
.sidebar { background: var(--bg); overflow-y: auto; padding: 14px 12px; display: flex; flex-direction: column; gap: 10px; }
.card { background: var(--surface); border: 1px solid var(--border); border-radius: 6px; padding: 12px 14px; }
.card-title { font-size: 10px; text-transform: uppercase; letter-spacing: 0.1em; color: var(--dim); margin-bottom: 10px; }
.stat-row { display: flex; justify-content: space-between; padding: 2px 0; }
.stat-l { color: var(--dim); }
.stat-v { color: var(--text); font-weight: 500; }
.stat-v.hi { color: var(--accent); }
.bar-row { display: flex; align-items: center; gap: 8px; margin: 3px 0; }
.bar-name { color: var(--dim); width: 130px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; flex-shrink: 0; }
.bar-track { flex: 1; height: 3px; background: var(--border); border-radius: 2px; overflow: hidden; }
.bar-fill { height: 100%; background: var(--accent); border-radius: 2px; transition: width .4s ease; }
.bar-count { color: var(--dim); width: 26px; text-align: right; font-size: 11px; }

/* Stream */
.stream { background: var(--bg); display: flex; flex-direction: column; min-height: 0; }
.stream-header { padding: 9px 16px; border-bottom: 1px solid var(--border); display: flex; align-items: center; gap: 10px; background: var(--surface); flex-shrink: 0; }
.stream-header-label { font-size: 10px; text-transform: uppercase; letter-spacing: 0.1em; color: var(--dim); }
.btn { background: var(--border); border: none; color: var(--dim); padding: 3px 10px; border-radius: 4px; cursor: pointer; font-size: 11px; font-family: inherit; }
.btn:hover { color: var(--text); }
.btn.on { background: #1c2a3a; color: var(--blue); }
.stream-body { flex: 1; overflow-y: auto; padding: 4px 0; }

/* Events */
.ev { display: flex; gap: 10px; padding: 5px 16px; align-items: baseline; border-bottom: 1px solid #161b22; }
.ev:hover { background: var(--surface); }
.ev-time { color: var(--dim); width: 54px; flex-shrink: 0; font-size: 11px; }
.badge { flex-shrink: 0; padding: 1px 7px; border-radius: 3px; font-size: 10px; font-weight: 700; letter-spacing: 0.06em; }
.b-start  { background: #0d2137; color: var(--blue); }
.b-tool   { background: #0d2614; color: var(--green); }
.b-done   { background: #1a1240; color: #a78bfa; }
.b-error  { background: #2d0f0f; color: var(--red); }
.b-sched  { background: #2b1f00; color: var(--yellow); }
.b-mem    { background: #0d2424; color: #2dd4bf; }
.b-cmd    { background: #1f1430; color: #c084fc; }
.ev-body { flex: 1; color: var(--text); overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }
.ev-body .d { color: var(--dim); }
.ev-body .err { color: var(--red); }
.run-tag { font-size: 10px; color: var(--dim); margin-left: 6px; }

/* Tab bar */
.tab-bar { display: flex; gap: 0; padding: 0 20px; border-bottom: 1px solid var(--border); background: var(--surface); flex-shrink: 0; }
.tab { background: none; border: none; border-bottom: 2px solid transparent; color: var(--dim); padding: 8px 16px; cursor: pointer; font-size: 12px; font-family: inherit; }
.tab.active { color: var(--text); border-bottom-color: var(--accent); }
.tab:hover:not(.active) { color: var(--text); }

/* Tab panels */
.tab-panel { display: none; flex: 1; min-height: 0; flex-direction: column; }
.tab-panel.active { display: flex; }

/* Details tab */
.details-body { flex: 1; overflow-y: auto; }
.details-toolbar { padding: 12px 20px; display: flex; align-items: center; gap: 12px; border-bottom: 1px solid var(--border); flex-shrink: 0; }
.details-btn { background: var(--surface); border: 1px solid var(--border); color: var(--text); padding: 4px 12px; border-radius: 5px; cursor: pointer; font-size: 12px; font-family: inherit; }
.details-btn:hover { border-color: var(--accent); }
.details-section { padding: 0 20px 24px; }
.details-section h3 { font-size: 10px; color: var(--dim); margin: 20px 0 10px; text-transform: uppercase; letter-spacing: 0.1em; }
.profiles-row { display: grid; grid-template-columns: 1fr 1fr; }
.mem-table { width: 100%; border-collapse: collapse; font-size: 13px; }
.mem-table th { text-align: left; color: var(--dim); font-weight: normal; padding: 5px 10px; border-bottom: 1px solid var(--border); font-size: 10px; text-transform: uppercase; letter-spacing: 0.05em; }
.mem-table td { padding: 8px 10px; border-bottom: 1px solid var(--border); vertical-align: top; line-height: 1.4; }
.mem-table tr:last-child td { border-bottom: none; }
.scope-tag { color: var(--dim); font-size: 11px; white-space: nowrap; }
.time-tag { color: var(--dim); font-size: 11px; white-space: nowrap; }
.profile-kv { display: grid; grid-template-columns: auto 1fr; gap: 4px 14px; font-size: 13px; padding: 4px 0; }
.profile-key { color: var(--dim); }
.summary-block { font-size: 13px; line-height: 1.5; padding: 8px 12px; background: var(--surface); border: 1px solid var(--border); border-radius: 6px; margin-bottom: 10px; }
.summary-user { color: var(--dim); font-size: 11px; margin-bottom: 6px; }
</style>
</head>
<body>

<header>
  <div class="pulse" id="pulse"></div>
  <span class="logo">HomeAgent</span>
  <span id="conn-label">connecting…</span>
  <div class="hdr-right">
    <span class="hdr-stat" id="hdr-cpu"><span id="v-cpu">—</span> CPU</span>
    <span class="hdr-stat" id="hdr-mem"><span id="v-mem">—</span> MB</span>
    <span class="hdr-stat">uptime <span id="v-uptime">—</span></span>
  </div>
</header>

<div class="tab-bar">
  <button class="tab active" data-tab="live">Live</button>
  <button class="tab" data-tab="details">Details</button>
  <button class="tab" data-tab="scheduler">Scheduler</button>
</div>

<div id="tab-live" class="tab-panel active">
<div class="layout">
  <div class="sidebar">
    <div class="card">
      <div class="card-title">Runs (last 500)</div>
      <div class="stat-row"><span class="stat-l">Total</span><span class="stat-v hi" id="s-total">—</span></div>
      <div class="stat-row"><span class="stat-l">Avg duration</span><span class="stat-v" id="s-avg">—</span></div>
      <div class="stat-row"><span class="stat-l">Input tokens</span><span class="stat-v" id="s-tin">—</span></div>
      <div class="stat-row"><span class="stat-l">Output tokens</span><span class="stat-v" id="s-tout">—</span></div>
    </div>
    <div class="card">
      <div class="card-title">Memory</div>
      <div class="stat-row"><span class="stat-l">Episodic total</span><span class="stat-v hi" id="m-total">—</span></div>
      <div class="stat-row"><span class="stat-l">Auto-extracted</span><span class="stat-v" id="m-auto">—</span></div>
      <div class="stat-row"><span class="stat-l">Manual</span><span class="stat-v" id="m-manual">—</span></div>
      <div class="stat-row"><span class="stat-l">Conv. messages</span><span class="stat-v" id="m-msgs">—</span></div>
      <div class="stat-row"><span class="stat-l">Summaries</span><span class="stat-v" id="m-sums">—</span></div>
    </div>
    <div class="card">
      <div class="card-title">Models</div>
      <div id="models-list"></div>
    </div>
    <div class="card">
      <div class="card-title">Tool Usage</div>
      <div id="tools-list"><span style="color:var(--dim);font-size:11px">No data yet</span></div>
    </div>
  </div>

  <div class="stream">
    <div class="stream-header">
      <span class="stream-header-label">Live Activity</span>
      <button class="btn on" id="scroll-btn" onclick="toggleScroll()">Auto-scroll</button>
      <button class="btn" onclick="clearFeed()">Clear</button>
      <span style="margin-left:auto;font-size:11px;color:var(--dim)" id="ev-count">0 events</span>
    </div>
    <div class="stream-body" id="feed"></div>
  </div>
</div>
</div>

<div id="tab-details" class="tab-panel">
  <div class="details-body">
    <div class="details-toolbar">
      <button class="details-btn" id="refresh-memory">↺ Refresh</button>
      <span id="memory-updated" style="font-size:11px;color:var(--dim)"></span>
    </div>
    <section class="details-section">
      <h3>Episodic Memories <span id="mem-count" style="font-size:10px;color:var(--dim)"></span></h3>
      <table class="mem-table">
        <thead><tr><th style="width:90px">Scope</th><th>Memory</th><th style="width:72px">Tier</th><th style="width:72px">Last used</th><th style="width:72px">Stored</th></tr></thead>
        <tbody id="mem-tbody"></tbody>
      </table>
    </section>
    <div class="profiles-row">
      <section class="details-section">
        <h3>User Profiles</h3>
        <div id="user-profiles"><span style="color:var(--dim)">—</span></div>
      </section>
      <section class="details-section">
        <h3>Household Profile</h3>
        <div id="household-profile"><span style="color:var(--dim)">—</span></div>
      </section>
    </div>
    <section class="details-section">
      <h3>Conversation Summaries</h3>
      <div id="conv-summaries"><span style="color:var(--dim)">—</span></div>
    </section>
  </div>
</div>

<div id="tab-scheduler" class="tab-panel">
  <div class="details-body">
    <div class="details-toolbar">
      <button class="details-btn" id="refresh-scheduler">&#8635; Refresh</button>
    </div>
    <section class="details-section">
      <h3>Reminders</h3>
      <table class="mem-table">
        <thead><tr><th>Due</th><th>Text</th><th style="width:80px">User</th><th style="width:72px">ID</th></tr></thead>
        <tbody id="sched-reminders"><tr><td colspan="4" style="color:var(--dim);padding:12px 10px">—</td></tr></tbody>
      </table>
    </section>
    <section class="details-section">
      <h3>Device Actions</h3>
      <table class="mem-table">
        <thead><tr><th>Due</th><th>Description</th><th>Tool</th><th style="width:80px">User</th><th style="width:72px">ID</th></tr></thead>
        <tbody id="sched-actions"><tr><td colspan="5" style="color:var(--dim);padding:12px 10px">—</td></tr></tbody>
      </table>
    </section>
    <section class="details-section">
      <h3>Scheduled Prompts</h3>
      <table class="mem-table">
        <thead><tr><th style="width:180px">Name</th><th style="width:160px">Schedule</th><th>Prompt</th><th style="width:72px">ID</th></tr></thead>
        <tbody id="sched-prompts"><tr><td colspan="4" style="color:var(--dim);padding:12px 10px">—</td></tr></tbody>
      </table>
    </section>
  </div>
</div>

<script>
// Propagate ?token= from the page URL to all API sub-requests
const _tok = new URLSearchParams(window.location.search).get('token');
const _authQ = _tok ? '?token=' + encodeURIComponent(_tok) : '';

let autoScroll = true;
let evCount = 0;
const MAX_EVENTS = 300;

function toggleScroll() {
  autoScroll = !autoScroll;
  const btn = document.getElementById('scroll-btn');
  btn.classList.toggle('on', autoScroll);
  btn.textContent = autoScroll ? 'Auto-scroll' : 'Paused';
}

function clearFeed() {
  document.getElementById('feed').innerHTML = '';
  evCount = 0;
  updateCount();
}

function updateCount() {
  document.getElementById('ev-count').textContent = evCount + ' events';
}

function fmtTime(ts) {
  return new Date(ts).toLocaleTimeString('en-GB', {hour:'2-digit',minute:'2-digit',second:'2-digit'});
}

function fmtUptime(s) {
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60);
  return h > 0 ? h + 'h ' + m + 'm' : m + 'm';
}

function addEvent(type, data) {
  const feed = document.getElementById('feed');
  const el = document.createElement('div');
  el.className = 'ev';

  const badges = {
    'run.start':    ['b-start','START'],
    'run.tool_call':['b-tool','TOOL'],
    'run.complete': ['b-done','DONE'],
    'run.error':    ['b-error','ERR'],
    'job.fire':     ['b-sched','SCHED'],
    'job.complete': ['b-sched','SCHED'],
    'job.error':    ['b-error','SCHED'],
    'mem.extract':  ['b-mem','MEM'],
    'mem.summarize':['b-mem','MEM'],
    'cmd.dispatch': ['b-cmd','CMD'],
  };
  const [cls, label] = badges[type] || ['b-start', type];

  let body = '';
  if (type === 'run.start') {
    const ctx = data.ctx_tokens ? ' · <span title="' + (data.ctx_chars || 0).toLocaleString() + ' chars · ' + (data.msg_count || 0) + ' msgs">~' + fmtK(data.ctx_tokens) + ' ctx</span>' : '';
    body = '<strong>' + (data.user_name || 'user') + '</strong> <span class="d">→ ' + shortModel(data.model || '') + ctx + '</span>';
  } else if (type === 'run.tool_call') {
    const dur = data.duration_ms ? ' <span class="d">' + data.duration_ms + 'ms</span>' : '';
    const errPart = data.success === false ? ' <span class="err">' + (data.error || 'failed') + '</span>' : '';
    body = '<strong>' + (data.tool || '?') + '</strong>' + dur + errPart;
  } else if (type === 'run.complete') {
    const tok = (data.input_tokens || 0) + (data.output_tokens || 0);
    const tokStr = tok ? ' · ' + tok + ' tok' : '';
    const toolNames = Array.isArray(data.tools) && data.tools.length
      ? data.tools.join(', ')
      : (data.tool_count === 0 ? 'no tools' : '');
    body = '<strong>' + toolNames + '</strong> <span class="d">' + (data.duration_ms || 0) + 'ms' + tokStr + '</span>';
  } else if (type === 'run.error') {
    body = '<span class="err">' + (data.error || 'unknown error') + '</span>';
  } else if (type === 'job.fire') {
    const desc = data.job === 'reminder' ? (data.text || '') : (data.tool || '') + (data.description ? ' · ' + data.description : '');
    body = '<span class="d">firing</span> <strong>' + (data.job || '?') + '</strong>' + (desc ? ' <span class="d">· ' + desc + '</span>' : '');
  } else if (type === 'job.complete') {
    const dur = data.duration_ms ? ' <span class="d">' + data.duration_ms + 'ms</span>' : '';
    const desc = data.job === 'reminder' ? (data.text || '') : (data.tool || '');
    body = '<strong>' + (data.job || '?') + '</strong> <span class="d">done</span>' + (desc ? ' · ' + desc : '') + dur;
  } else if (type === 'job.error') {
    const desc = data.description || data.tool || '';
    body = '<strong>' + (data.job || '?') + '</strong> <span class="err">failed</span>' + (desc ? ' · ' + desc : '');
  } else if (type === 'mem.extract') {
    const facts = Array.isArray(data.facts) ? data.facts : [];
    const lines = facts.map(f => '<span class="d">· </span>' + f).join(' ');
    body = '<strong>stored ' + facts.length + ' fact' + (facts.length !== 1 ? 's' : '') + '</strong>'
      + (lines ? ' <span class="d">— ' + lines + '</span>' : '');
  } else if (type === 'mem.summarize') {
    const n = data.messages_compressed || 0;
    const snippet = data.summary ? data.summary.slice(0, 120) + (data.summary.length > 120 ? '…' : '') : '';
    body = '<strong>compressed ' + n + ' msgs</strong>'
      + (snippet ? ' <span class="d">— ' + snippet + '</span>' : '');
  } else if (type === 'cmd.dispatch') {
    const dur = data.duration_ms ? ' <span class="d">' + data.duration_ms + 'ms</span>' : '';
    const who = data.user_id ? ' <span class="d">by ' + data.user_id.slice(0, 8) + '</span>' : '';
    const status = data.success === false ? ' <span class="err">failed</span>' : '';
    body = '<strong>/' + (data.command || '?') + '</strong>' + who + dur + status;
  }

  const runTag = data.run_id ? '<span class="run-tag">' + data.run_id.slice(0,8) + '</span>' : '';
  el.innerHTML =
    '<span class="ev-time">' + fmtTime(data.ts || new Date().toISOString()) + '</span>' +
    '<span class="badge ' + cls + '">' + label + '</span>' +
    '<span class="ev-body">' + body + runTag + '</span>';

  feed.appendChild(el);
  evCount++;
  updateCount();

  while (feed.children.length > MAX_EVENTS) feed.removeChild(feed.firstChild);
  if (autoScroll) feed.scrollTop = feed.scrollHeight;
}

function shortModel(m) {
  const parts = m.split('-');
  return parts.length > 3 ? parts.slice(-3).join('-') : m;
}

function fmtK(n) {
  return n >= 1000 ? (n / 1000).toFixed(1) + 'k' : String(n);
}

// Stats
async function fetchStats() {
  try {
    const r = await fetch('/admin/stats' + _authQ);
    if (!r.ok) return;
    const d = await r.json();
    document.getElementById('v-cpu').textContent = d.system.cpu_percent + '%';
    document.getElementById('v-mem').textContent = d.system.memory_mb;
    document.getElementById('v-uptime').textContent = fmtUptime(d.system.uptime_seconds);
    document.getElementById('s-total').textContent = d.runs.total;
    document.getElementById('s-avg').textContent = d.runs.avg_duration_ms + ' ms';
    document.getElementById('s-tin').textContent = (d.tokens.input || 0).toLocaleString();
    document.getElementById('s-tout').textContent = (d.tokens.output || 0).toLocaleString();

    if (d.memory) {
      document.getElementById('m-total').textContent = d.memory.episodic_total;
      document.getElementById('m-auto').textContent = d.memory.episodic_auto;
      document.getElementById('m-manual').textContent = d.memory.episodic_manual;
      document.getElementById('m-msgs').textContent = d.memory.messages_total;
      document.getElementById('m-sums').textContent = d.memory.summaries_total;
    }

    const ml = document.getElementById('models-list');
    ml.innerHTML = '';
    for (const [model, count] of Object.entries(d.models)) {
      const row = document.createElement('div');
      row.className = 'stat-row';
      row.innerHTML = '<span class="stat-l" title="' + model + '">' + shortModel(model) + '</span><span class="stat-v">' + count + '</span>';
      ml.appendChild(row);
    }

    const tl = document.getElementById('tools-list');
    const entries = Object.entries(d.tools).sort((a, b) => b[1] - a[1]);
    if (!entries.length) { tl.innerHTML = '<span style="color:var(--dim);font-size:11px">No data yet</span>'; return; }
    tl.innerHTML = '';
    const max = entries[0][1] || 1;
    for (const [tool, count] of entries) {
      const row = document.createElement('div');
      row.className = 'bar-row';
      const pct = Math.round((count / max) * 100);
      row.innerHTML =
        '<span class="bar-name" title="' + tool + '">' + tool + '</span>' +
        '<div class="bar-track"><div class="bar-fill" style="width:' + pct + '%"></div></div>' +
        '<span class="bar-count">' + count + '</span>';
      tl.appendChild(row);
    }
  } catch (e) {}
}

// XSS-safe escaping
function esc(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

// Relative time display
function relTime(iso) {
  if (!iso) return '';
  const diff = Date.now() - new Date(iso).getTime();
  if (diff < 60000) return 'just now';
  if (diff < 3600000) return Math.floor(diff/60000) + 'm ago';
  if (diff < 86400000) return Math.floor(diff/3600000) + 'h ago';
  return Math.floor(diff/86400000) + 'd ago';
}

// Tab switching
document.querySelectorAll('.tab').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
    btn.classList.add('active');
    document.getElementById('tab-' + btn.dataset.tab).classList.add('active');
    if (btn.dataset.tab === 'details') loadMemory();
    if (btn.dataset.tab === 'scheduler') loadScheduler();
  });
});

// Memory details loader
async function loadMemory() {
  try {
    const r = await fetch('/admin/memory' + _authQ);
    if (!r.ok) return;
    const d = await r.json();

    const tierColor = {critical:'#e05252',important:'#e09a32',normal:'var(--dim)',ephemeral:'#6a7a8a'};
    document.getElementById('mem-count').textContent = '(' + d.episodic.length + ')';
    document.getElementById('mem-tbody').innerHTML = d.episodic.length
      ? d.episodic.map(m => {
          const color = tierColor[m.importance] || 'var(--dim)';
          return '<tr><td class="scope-tag">' + esc(m.scope) + '</td>' +
            '<td>' + esc(m.content) + '</td>' +
            '<td class="time-tag" style="color:' + color + '">' + esc(m.importance) + '</td>' +
            '<td class="time-tag">' + (m.last_used_at ? relTime(m.last_used_at) : '—') + '</td>' +
            '<td class="time-tag">' + relTime(m.created_at) + '</td></tr>';
        }).join('')
      : '<tr><td colspan="5" style="color:var(--dim);padding:12px 10px">No memories yet</td></tr>';

    document.getElementById('user-profiles').innerHTML = d.user_profiles.length
      ? d.user_profiles.map(p =>
          '<div style="margin-bottom:14px">' +
          '<div class="summary-user">' + esc(p.user) + '</div>' +
          '<div class="profile-kv">' +
          Object.entries(p.data).map(([k,v]) =>
            '<span class="profile-key">' + esc(k) + '</span><span>' + esc(String(v)) + '</span>'
          ).join('') + '</div></div>'
        ).join('')
      : '<span style="color:var(--dim)">No profiles yet</span>';

    const hp = d.household_profile;
    document.getElementById('household-profile').innerHTML = Object.keys(hp.data).length
      ? '<div class="profile-kv">' +
        Object.entries(hp.data).map(([k,v]) =>
          '<span class="profile-key">' + esc(k) + '</span><span>' + esc(String(v)) + '</span>'
        ).join('') + '</div>'
      : '<span style="color:var(--dim)">No profile yet</span>';

    document.getElementById('conv-summaries').innerHTML = d.summaries.length
      ? d.summaries.map(s =>
          '<div class="summary-block">' +
          '<div class="summary-user">' + esc(s.user) + ' — ' + relTime(s.created_at) + '</div>' +
          '<div>' + esc(s.text) + '</div></div>'
        ).join('')
      : '<span style="color:var(--dim)">No summaries yet</span>';

    document.getElementById('memory-updated').textContent =
      'Updated ' + new Date().toLocaleTimeString();
  } catch(e) {}
}

document.getElementById('refresh-memory').addEventListener('click', loadMemory);

// Scheduler tab loader
async function loadScheduler() {
  try {
    const r = await fetch('/admin/scheduler' + _authQ);
    if (!r.ok) return;
    const d = await r.json();

    document.getElementById('sched-reminders').innerHTML = d.reminders.length
      ? d.reminders.map(r =>
          '<tr><td class="time-tag">' + esc(r.scheduled_at) + '</td><td>' + esc(r.text) +
          '</td><td class="mem-id">' + esc(r.user_id.slice(0,8)) +
          '</td><td class="mem-id">' + esc(r.id.slice(0,8)) + '</td></tr>'
        ).join('')
      : '<tr><td colspan="4" style="color:var(--dim);padding:12px 10px">No active reminders</td></tr>';

    document.getElementById('sched-actions').innerHTML = d.actions.length
      ? d.actions.map(a =>
          '<tr><td class="time-tag">' + esc(a.scheduled_at) + '</td><td>' + esc(a.description) +
          '</td><td class="mem-id">' + esc(a.tool) +
          '</td><td class="mem-id">' + esc(a.user_id.slice(0,8)) +
          '</td><td class="mem-id">' + esc(a.id.slice(0,8)) + '</td></tr>'
        ).join('')
      : '<tr><td colspan="5" style="color:var(--dim);padding:12px 10px">No active actions</td></tr>';

    const sp = d.scheduled_prompts || [];
    document.getElementById('sched-prompts').innerHTML = sp.length
      ? sp.map(p =>
          '<tr><td>' + esc(p.name) + '</td>' +
          '<td class="time-tag">' + esc(p.recurrence) + '</td>' +
          '<td style="color:var(--dim)">' + esc(p.prompt) + '</td>' +
          '<td class="mem-id">' + esc(p.id.slice(0,8)) + '</td></tr>'
        ).join('')
      : '<tr><td colspan="4" style="color:var(--dim);padding:12px 10px">No scheduled prompts</td></tr>';
  } catch(e) {}
}

document.getElementById('refresh-scheduler').addEventListener('click', loadScheduler);

fetchStats();
setInterval(fetchStats, 10000);

// SSE
function connectSSE() {
  const es = new EventSource('/admin/stream' + _authQ);
  const pulse = document.getElementById('pulse');
  const label = document.getElementById('conn-label');

  es.onopen = () => {
    pulse.className = 'pulse';
    label.className = 'ok';
    label.textContent = 'Connected';
  };
  es.onerror = () => {
    pulse.className = 'pulse off';
    label.className = 'err';
    label.textContent = 'Reconnecting…';
    es.close();
    setTimeout(connectSSE, 3000);
  };

  ['run.start','run.tool_call','run.complete','run.error','job.fire','job.complete','job.error','mem.extract','mem.summarize','cmd.dispatch'].forEach(type => {
    es.addEventListener(type, e => {
      try { addEvent(type, JSON.parse(e.data)); } catch(_) {}
    });
  });
}

connectSSE();
</script>
</body>
</html>"""
