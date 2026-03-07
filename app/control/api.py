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
            # Then stream new events
            while True:
                try:
                    event = await asyncio.wait_for(q.get(), timeout=30.0)
                    yield _format_sse(event)
                except asyncio.TimeoutError:
                    yield ": heartbeat\n\n"
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
  --text: #c9d1d9; --dim: #484f58; --accent: #7c5cfc;
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
.ev-body { flex: 1; color: var(--text); overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }
.ev-body .d { color: var(--dim); }
.ev-body .err { color: var(--red); }
.run-tag { font-size: 10px; color: #30363d; margin-left: 6px; }
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

<script>
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
    const tools = data.tool_count != null ? data.tool_count + ' tool' + (data.tool_count !== 1 ? 's' : '') : '';
    body = tools + ' <span class="d">' + (data.duration_ms || 0) + 'ms' + tokStr + '</span>';
  } else if (type === 'run.error') {
    body = '<span class="err">' + (data.error || 'unknown error') + '</span>';
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
    const r = await fetch('/admin/stats');
    if (!r.ok) return;
    const d = await r.json();
    document.getElementById('v-cpu').textContent = d.system.cpu_percent + '%';
    document.getElementById('v-mem').textContent = d.system.memory_mb;
    document.getElementById('v-uptime').textContent = fmtUptime(d.system.uptime_seconds);
    document.getElementById('s-total').textContent = d.runs.total;
    document.getElementById('s-avg').textContent = d.runs.avg_duration_ms + ' ms';
    document.getElementById('s-tin').textContent = (d.tokens.input || 0).toLocaleString();
    document.getElementById('s-tout').textContent = (d.tokens.output || 0).toLocaleString();

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

fetchStats();
setInterval(fetchStats, 10000);

// SSE
function connectSSE() {
  const es = new EventSource('/admin/stream');
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

  ['run.start','run.tool_call','run.complete','run.error'].forEach(type => {
    es.addEventListener(type, e => {
      try { addEvent(type, JSON.parse(e.data)); } catch(_) {}
    });
  });
}

connectSSE();
</script>
</body>
</html>"""
