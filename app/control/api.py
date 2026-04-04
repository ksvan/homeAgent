from __future__ import annotations

import asyncio
import json
import logging
import pathlib
import time
from typing import Any, AsyncGenerator

from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel

from app.control.auth import require_admin_auth

_auth = [Depends(require_admin_auth)]

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])

# Shutdown signal: set this before stopping the server so open SSE connections
# exit cleanly and uvicorn can drain without being force-cancelled.
_stream_shutdown = asyncio.Event()


def signal_stream_shutdown() -> None:
    """Signal all active admin SSE streams to close gracefully."""
    _stream_shutdown.set()


@router.get("", response_class=HTMLResponse)
async def admin_page() -> HTMLResponse:
    return HTMLResponse(
        content=_ADMIN_HTML,
        headers={"Cache-Control": "no-store, must-revalidate"},
    )


@router.get("/stats", dependencies=_auth)
async def admin_stats() -> dict[str, Any]:
    import psutil
    from sqlmodel import select

    from app.db import cache_session
    from app.models.cache import AgentRunLog

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

    # Control-loop stats from users.db
    from app.control.dispatcher import get_dispatcher_running
    from app.control.event_bus import bus_size
    from app.db import users_session
    from app.models.events import EventRule

    with users_session() as usession:
        rules_all = usession.exec(select(EventRule.id)).all()
        rules_enabled = usession.exec(
            select(EventRule.id).where(EventRule.enabled == True)  # noqa: E712
        ).all()
        # Active tasks that carry a control-loop context block
        from sqlalchemy import text as _sql_text
        ctrl_active_row = usession.exec(  # type: ignore[call-overload]
            _sql_text(
                "SELECT count(*) FROM task"
                " WHERE status='ACTIVE'"
                " AND json_extract(context,'$.control') IS NOT NULL"
            )
        ).one()
        ctrl_tasks_active = int(ctrl_active_row[0])

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
        "control_loop": {
            "dispatcher_running": get_dispatcher_running(),
            "event_bus_size": bus_size(),
            "event_rules_total": len(rules_all),
            "event_rules_enabled": len(rules_enabled),
            "control_tasks_active": ctrl_tasks_active,
        },
    }


@router.get("/stream", dependencies=_auth)
async def admin_stream() -> StreamingResponse:
    from app.control.events import get_recent_events, subscribe, unsubscribe

    async def event_generator() -> AsyncGenerator[str, None]:
        q = subscribe()
        try:
            # Flush an SSE comment immediately so the browser fires onopen
            # (EventSource needs at least one body byte before triggering it)
            yield ": ok\n\n"
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


@router.get("/memory", dependencies=_auth)
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


@router.get("/scheduler", dependencies=_auth)
async def admin_scheduler() -> dict[str, Any]:
    import json as _json

    from sqlmodel import col, select

    from app.db import users_session
    from app.models.scheduled_prompts import ScheduledPrompt, ScheduledPromptLink
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
        # Batch-load all links for enabled prompts
        sp_ids = [sp.id for sp in sps]
        all_links = (
            session.exec(
                select(ScheduledPromptLink).where(col(ScheduledPromptLink.prompt_id).in_(sp_ids))
            ).all()
            if sp_ids
            else []
        )

    links_by_prompt: dict[str, list] = {}
    for ln in all_links:
        links_by_prompt.setdefault(ln.prompt_id, []).append(
            {"entity_type": ln.entity_type, "entity_id": ln.entity_id, "role": ln.role}
        )

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

    scheduled_prompts = []
    for sp in sps:
        scheduled_prompts.append({
            "id": sp.id,
            "user_id": sp.user_id,
            "name": sp.name,
            "recurrence": recurrence_label(sp.recurrence, sp.time_of_day),
            "prompt": sp.prompt[:120] + ("…" if len(sp.prompt) > 120 else ""),
            "behavior_kind": sp.behavior_kind or "generic_prompt",
            "goal": sp.goal or "",
            "last_status": sp.last_status,
            "last_fired_at": sp.last_fired_at.isoformat() if sp.last_fired_at else None,
            "last_delivered_at": sp.last_delivered_at.isoformat() if sp.last_delivered_at else None,
            "last_result_preview": sp.last_result_preview or "",
            "linked_entities": links_by_prompt.get(sp.id, []),
        })

    return {"reminders": reminders, "actions": actions, "scheduled_prompts": scheduled_prompts}


@router.get("/scheduler/runs/{prompt_id}", dependencies=_auth)
async def admin_scheduler_runs(prompt_id: str) -> dict[str, Any]:
    """Return the last 20 run-history rows for a scheduled prompt."""
    from sqlmodel import col, select

    from app.db import users_session
    from app.models.scheduled_prompts import ScheduledPromptRun

    with users_session() as session:
        runs = session.exec(
            select(ScheduledPromptRun)
            .where(col(ScheduledPromptRun.prompt_id) == prompt_id)
            .order_by(col(ScheduledPromptRun.fired_at).desc())
            .limit(20)
        ).all()

    return {
        "runs": [
            {
                "id": r.id,
                "fired_at": r.fired_at.isoformat() if r.fired_at else None,
                "finished_at": r.finished_at.isoformat() if r.finished_at else None,
                "status": r.status,
                "skip_reason": r.skip_reason,
                "run_id": r.run_id,
                "output_preview": r.output_preview or "",
            }
            for r in runs
        ]
    }


@router.post("/scheduler/{prompt_id}/run-now", dependencies=_auth)
async def admin_run_prompt_now(prompt_id: str) -> dict[str, str]:
    """Fire a scheduled prompt immediately (for debugging)."""
    import asyncio

    from app.db import users_session
    from app.models.scheduled_prompts import ScheduledPrompt
    from app.scheduler.jobs import fire_scheduled_prompt

    with users_session() as session:
        sp = session.get(ScheduledPrompt, prompt_id)
        if sp is None:
            return {"status": "error", "message": "Prompt not found"}
        if not sp.enabled:
            return {"status": "error", "message": "Prompt is disabled"}
        kwargs = {
            "prompt_id": sp.id,
            "user_id": sp.user_id,
            "household_id": sp.household_id,
            "channel_user_id": sp.channel_user_id,
            "prompt_text": sp.prompt,
            "name": sp.name,
            "is_one_shot": False,
        }

    asyncio.ensure_future(fire_scheduled_prompt(**kwargs))
    return {"status": "ok", "message": f"Fired '{sp.name}'"}


@router.get("/world-model", dependencies=_auth)
async def admin_world_model() -> dict[str, Any]:

    from sqlmodel import select

    from app.db import users_session
    from app.models.users import Household
    from app.world.repository import WorldModelRepository

    with users_session() as session:
        household = session.exec(select(Household)).first()

    if not household:
        return {"error": "No household found"}

    snap = WorldModelRepository.get_full_snapshot(household.id)

    def _serialize(items: list) -> list[dict[str, Any]]:
        return [
            {k: v for k, v in row.__dict__.items() if not k.startswith("_")}
            for row in items
        ]

    return {
        "household_id": household.id,
        "members": _serialize(snap.members),
        "interests": _serialize(snap.interests),
        "goals": _serialize(snap.goals),
        "activities": _serialize(snap.activities),
        "places": _serialize(snap.places),
        "devices": _serialize(snap.devices),
        "calendars": _serialize(snap.calendars),
        "routines": _serialize(snap.routines),
        "relationships": _serialize(snap.relationships),
        "facts": _serialize(snap.facts),
    }


# ---------------------------------------------------------------------------
# World-model write endpoints
# ---------------------------------------------------------------------------

class _FactBody(BaseModel):
    scope: str
    key: str
    value: str

class _AliasBody(BaseModel):
    entity_type: str   # "place" | "deviceentity" | "householdmember"
    entity_id: str
    alias: str

class _RoutineBody(BaseModel):
    name: str
    description: str = ""
    kind: str = ""

class _MemberBody(BaseModel):
    name: str
    role: str = "member"  # "admin" | "member" | "child" | "guest"

class _MemberDetailBody(BaseModel):
    detail_type: str   # "interest" | "activity" | "goal"
    member_id: str
    name: str
    schedule_hint: str = ""
    notes: str = ""


def _get_household_id() -> str:
    from sqlmodel import select

    from app.db import users_session
    from app.models.users import Household
    with users_session() as session:
        household = session.exec(select(Household)).first()
    return household.id if household else ""


@router.put("/world-model/member", dependencies=_auth)
async def admin_upsert_member(body: _MemberBody) -> dict[str, Any]:
    from app.control.events import emit
    from app.world.repository import WorldModelRepository as repo
    hid = _get_household_id()
    if not hid:
        return {"error": "No household found"}
    member = repo.upsert_member(
        hid, name=body.name, role=body.role,
        source="admin_authored",
    )
    emit("world.update", {"entity_type": "member", "action": "upsert", "name": member.name})
    return {"ok": True, "name": member.name, "role": member.role}


@router.put("/world-model/fact", dependencies=_auth)
async def admin_upsert_fact(body: _FactBody) -> dict[str, Any]:
    from app.control.events import emit
    from app.world.repository import WorldModelRepository as repo
    hid = _get_household_id()
    if not hid:
        return {"error": "No household found"}
    repo.upsert_world_fact(
        hid, scope=body.scope, key=body.key, value=body.value,
        source="admin_authored", overwrite=True,
    )
    emit("world.update", {"entity_type": "fact", "action": "upsert", "key": body.key})
    return {"ok": True, "scope": body.scope, "key": body.key}


@router.delete("/world-model/fact/{fact_id}", dependencies=_auth)
async def admin_delete_fact(fact_id: str) -> dict[str, Any]:
    from app.control.events import emit
    from app.world.repository import WorldModelRepository as repo
    ok = repo.delete_entity("worldfact", fact_id)
    if ok:
        emit("world.update", {"entity_type": "fact", "action": "delete", "id": fact_id})
    return {"ok": ok}


@router.put("/world-model/routine", dependencies=_auth)
async def admin_upsert_routine(body: _RoutineBody) -> dict[str, Any]:
    from app.control.events import emit
    from app.world.repository import WorldModelRepository as repo
    hid = _get_household_id()
    if not hid:
        return {"error": "No household found"}
    repo.upsert_routine(
        hid, name=body.name, description=body.description,
        kind=body.kind, source="admin_authored",
    )
    emit("world.update", {"entity_type": "routine", "action": "upsert", "name": body.name})
    return {"ok": True, "name": body.name}


@router.put("/world-model/alias", dependencies=_auth)
async def admin_add_alias(body: _AliasBody) -> dict[str, Any]:
    from app.control.events import emit
    from app.world.repository import WorldModelRepository as repo
    hid = _get_household_id()
    if not hid:
        return {"error": "No household found"}
    ok = repo.add_alias(hid, body.entity_type, body.entity_id, body.alias)
    if ok:
        emit(
            "world.update",
            {"entity_type": body.entity_type, "action": "alias_added", "alias": body.alias},
        )
    return {"ok": ok}


@router.put("/world-model/member-detail", dependencies=_auth)
async def admin_upsert_member_detail(body: _MemberDetailBody) -> dict[str, Any]:
    from app.control.events import emit
    from app.world.repository import WorldModelRepository as repo
    hid = _get_household_id()
    if not hid:
        return {"error": "No household found"}

    if body.detail_type == "interest":
        repo.upsert_interest(hid, member_id=body.member_id, name=body.name,
                             notes=body.notes, source="admin_authored")
    elif body.detail_type == "activity":
        repo.upsert_activity(hid, member_id=body.member_id, name=body.name,
                             schedule_hint=body.schedule_hint, notes=body.notes,
                             source="admin_authored")
    elif body.detail_type == "goal":
        repo.upsert_goal(hid, member_id=body.member_id, name=body.name,
                         notes=body.notes, source="admin_authored")
    else:
        return {"error": f"Unknown detail_type: {body.detail_type}"}

    emit("world.update", {"entity_type": body.detail_type, "action": "upsert", "name": body.name})
    return {"ok": True, "detail_type": body.detail_type, "name": body.name}


@router.delete("/world-model/entity/{entity_type}/{entity_id}", dependencies=_auth)
async def admin_delete_entity(entity_type: str, entity_id: str) -> dict[str, Any]:
    from app.control.events import emit
    from app.world.repository import WorldModelRepository as repo
    ok = repo.delete_entity(entity_type, entity_id)
    if ok:
        emit("world.update", {"entity_type": entity_type, "action": "delete", "id": entity_id})
    return {"ok": ok}


# ---------------------------------------------------------------------------
# World-model proposals  (Phase 4)
# ---------------------------------------------------------------------------


@router.get("/world-model/proposals", dependencies=_auth)
async def admin_list_proposals() -> dict[str, Any]:
    from app.world.repository import WorldModelRepository as repo
    hid = _get_household_id()
    if not hid:
        return {"proposals": []}
    proposals = repo.get_recent_proposals(hid)
    return {
        "proposals": [
            {
                "id": p.id,
                "proposal_type": p.proposal_type,
                "entity_type": p.entity_type,
                "payload": json.loads(p.payload_json),
                "reason": p.reason,
                "confidence": p.confidence,
                "status": p.status,
                "created_at": p.created_at.isoformat() if p.created_at else None,
                "reviewed_at": p.reviewed_at.isoformat() if p.reviewed_at else None,
                "reviewed_by": p.reviewed_by,
            }
            for p in proposals
        ],
        "pending_count": sum(1 for p in proposals if p.status == "pending"),
    }


class _ProposalDecision(BaseModel):
    decision: str  # "accepted" | "rejected"


@router.post("/world-model/proposals/{proposal_id}/review", dependencies=_auth)
async def admin_review_proposal(proposal_id: str, body: _ProposalDecision) -> dict[str, Any]:
    from app.control.events import emit
    from app.world.repository import WorldModelRepository as repo

    if body.decision not in ("accepted", "rejected"):
        return {"error": "decision must be 'accepted' or 'rejected'"}

    p = repo.review_proposal(proposal_id, body.decision)
    if p is None:
        return {"error": "Proposal not found or already reviewed"}

    if body.decision == "accepted":
        _apply_accepted_proposal(p)

    emit(
        "world.update",
        {"action": "proposal_reviewed", "proposal_id": proposal_id, "decision": body.decision},
    )
    return {"ok": True, "status": p.status}


class _BulkDecision(BaseModel):
    proposal_ids: list[str]
    decision: str  # "accepted" | "rejected"


@router.post("/world-model/proposals/bulk", dependencies=_auth)
async def admin_bulk_review(body: _BulkDecision) -> dict[str, Any]:
    from app.control.events import emit
    from app.world.repository import WorldModelRepository as repo

    if body.decision not in ("accepted", "rejected"):
        return {"error": "decision must be 'accepted' or 'rejected'"}

    reviewed = 0
    for pid in body.proposal_ids:
        p = repo.review_proposal(pid, body.decision)
        if p is not None:
            if body.decision == "accepted":
                _apply_accepted_proposal(p)
            reviewed += 1

    if reviewed:
        emit(
            "world.update",
            {"action": "bulk_review", "count": reviewed, "decision": body.decision},
        )
    return {"ok": True, "reviewed": reviewed}


def _apply_accepted_proposal(p: object) -> None:
    """Apply an accepted proposal to the world model."""
    import json as _json

    from app.world.repository import WorldModelRepository as repo

    payload = _json.loads(p.payload_json)  # type: ignore[union-attr]
    ptype = p.proposal_type  # type: ignore[union-attr]
    hid = p.household_id  # type: ignore[union-attr]

    try:
        if ptype == "fact":
            val = payload.get("value", "")
            repo.upsert_world_fact(
                household_id=hid,
                scope=payload.get("scope", "household"),
                key=payload["key"],
                value_json=_json.dumps(val) if not isinstance(val, str) else val,
                source="proposal_accepted",
            )
        elif ptype == "alias":
            entity_type = payload.get("entity_type", "")
            entity_name = payload.get("entity_name", "")
            alias = payload.get("alias", "")
            finder = {
                "member": repo.find_member_by_name,
                "place": repo.find_place_by_name,
                "device": repo.find_device_by_name,
            }.get(entity_type)
            if finder and alias:
                entity = finder(hid, entity_name)
                if entity:
                    repo.add_alias(hid, entity_type, entity.id, alias)
        elif ptype == "interest":
            member = repo.find_member_by_name(hid, payload.get("member_name", ""))
            if member:
                repo.upsert_interest(hid, member.id, name=payload["name"],
                                     notes=payload.get("notes", ""), source="proposal_accepted")
        elif ptype == "activity":
            member = repo.find_member_by_name(hid, payload.get("member_name", ""))
            if member:
                repo.upsert_activity(hid, member.id, name=payload["name"],
                                     schedule_hint=payload.get("schedule_hint", ""),
                                     notes=payload.get("notes", ""), source="proposal_accepted")
        elif ptype == "goal":
            member = repo.find_member_by_name(hid, payload.get("member_name", ""))
            if member:
                repo.upsert_goal(hid, member.id, name=payload["name"],
                                 notes=payload.get("notes", ""), source="proposal_accepted")
        elif ptype == "routine":
            repo.upsert_routine(
                household_id=hid,
                name=payload["name"],
                description=payload.get("description", ""),
                kind=payload.get("kind", "custom"),
                source="proposal_accepted",
            )
    except Exception:
        logger.warning("Failed to apply accepted proposal %s", p.id, exc_info=True)  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# Task endpoints
# ---------------------------------------------------------------------------

@router.get("/tasks", dependencies=_auth)
async def admin_tasks() -> dict[str, Any]:
    """List all non-terminal tasks with steps and links."""
    from sqlmodel import select

    from app.db import users_session
    from app.models.tasks import TERMINAL_STATUSES, Task, TaskLink, TaskStep
    from app.models.users import User

    with users_session() as session:
        tasks = session.exec(
            select(Task)
            .where(Task.status.notin_(TERMINAL_STATUSES))  # type: ignore[attr-defined]
            .order_by(Task.updated_at.desc())  # type: ignore[union-attr]
        ).all()

        # Also fetch recently completed tasks (last 20)
        recent_done = session.exec(
            select(Task)
            .where(Task.status.in_(TERMINAL_STATUSES))  # type: ignore[attr-defined]
            .order_by(Task.updated_at.desc())  # type: ignore[union-attr]
            .limit(20)
        ).all()

        all_tasks = list(tasks) + list(recent_done)
        task_ids = [t.id for t in all_tasks]

        steps_by_task: dict[str, list] = {}
        links_by_task: dict[str, list] = {}
        if task_ids:
            all_steps = session.exec(
                select(TaskStep).where(TaskStep.task_id.in_(task_ids))  # type: ignore[attr-defined]
            ).all()
            all_links = session.exec(
                select(TaskLink).where(TaskLink.task_id.in_(task_ids))  # type: ignore[attr-defined]
            ).all()
            for s in all_steps:
                steps_by_task.setdefault(s.task_id, []).append(s)
            for ln in all_links:
                links_by_task.setdefault(ln.task_id, []).append(ln)

        # Resolve user names
        user_ids = {t.user_id for t in all_tasks}
        user_names: dict[str, str] = {}
        if user_ids:
            users = session.exec(select(User).where(User.id.in_(list(user_ids)))).all()
            user_names = {u.id: u.name for u in users}

    result = []
    for t in all_tasks:
        import json as _json
        steps = sorted(steps_by_task.get(t.id, []), key=lambda s: s.step_index)
        links = links_by_task.get(t.id, [])
        try:
            ctx_data = _json.loads(t.context or "{}")
            control = ctx_data.get("control") or None
        except Exception:
            control = None
        result.append({
            "id": t.id,
            "title": t.title,
            "task_kind": t.task_kind or "legacy",
            "status": t.status,
            "summary": t.summary,
            "awaiting_input_hint": t.awaiting_input_hint,
            "current_step": t.current_step,
            "user": user_names.get(t.user_id, t.user_id[:8]),
            "created_at": t.created_at.isoformat() if t.created_at else None,
            "updated_at": t.updated_at.isoformat() if t.updated_at else None,
            "control": control,
            "steps": [
                {"index": s.step_index, "title": s.title, "status": s.status, "type": s.step_type}
                for s in steps
            ],
            "links": [
                {"entity_type": ln.entity_type, "entity_id": ln.entity_id, "role": ln.role}
                for ln in links
            ],
        })

    return {"tasks": result}


class _TaskActionBody(BaseModel):
    action: str  # "cancel" | "resume"


@router.post("/tasks/{task_id}/action", dependencies=_auth)
async def admin_task_action(task_id: str, body: _TaskActionBody) -> dict[str, str]:
    """Admin cancel or resume a task."""
    from app.control.events import emit
    from app.tasks.repository import TaskRepository

    repo = TaskRepository()
    task = repo.get_task(task_id)
    if task is None:
        return {"error": "Task not found"}

    if body.action == "cancel":
        try:
            repo.transition_status(task_id, "CANCELLED")
            repo.update_task(task_id, summary="Cancelled by admin")
            emit("task.cancel", {"task_id": task_id, "reason": "admin"})
            return {"status": "cancelled"}
        except ValueError as exc:
            return {"error": str(exc)}
    elif body.action == "resume":
        try:
            repo.transition_status(task_id, "ACTIVE")
            repo.update_task(task_id, awaiting_input_hint=None)
            emit("task.update", {"task_id": task_id, "summary": "Resumed by admin"})
            return {"status": "resumed"}
        except ValueError as exc:
            return {"error": str(exc)}
    else:
        return {"error": f"Unknown action: {body.action}"}


# ---------------------------------------------------------------------------
# Event rules
# ---------------------------------------------------------------------------


@router.get("/event-rules", dependencies=_auth)
async def admin_event_rules() -> dict[str, Any]:
    """List all EventRule records for the household."""
    from sqlmodel import select

    from app.db import users_session
    from app.models.events import EventRule

    with users_session() as session:
        rules = session.exec(select(EventRule).order_by(EventRule.created_at.desc())).all()

    return {
        "rules": [
            {
                "id": r.id,
                "name": r.name,
                "source": r.source,
                "event_type": r.event_type,
                "entity_id": r.entity_id,
                "capability": r.capability,
                "value_filter_json": r.value_filter_json,
                "condition_json": r.condition_json,
                "cooldown_minutes": r.cooldown_minutes,
                "prompt_template": r.prompt_template,
                "enabled": r.enabled,
                "run_mode": r.run_mode,
                "task_kind_default": r.task_kind_default,
                "correlation_key_tpl": r.correlation_key_tpl,
                "last_triggered_at": r.last_triggered_at.isoformat() if r.last_triggered_at else None,  # noqa: E501
                "created_at": r.created_at.isoformat(),
            }
            for r in rules
        ]
    }


# ---------------------------------------------------------------------------
# Embedded admin UI
# ---------------------------------------------------------------------------

try:
    _ADMIN_HTML = (pathlib.Path(__file__).with_name("dashboard.html")).read_text()
except FileNotFoundError:
    logger.error("dashboard.html not found next to api.py — admin UI will be unavailable")
    _ADMIN_HTML = "<html><body><pre>Admin UI unavailable: dashboard.html missing.</pre></body></html>"  # noqa: E501
