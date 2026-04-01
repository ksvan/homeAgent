from __future__ import annotations

import asyncio
import json
import logging
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
    import json as _json

    from app.world.repository import WorldModelRepository, WorldModelSnapshot

    from sqlmodel import select
    from app.db import users_session
    from app.models.users import Household

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
    from app.world.repository import WorldModelRepository as repo
    from app.control.events import emit
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
    from app.world.repository import WorldModelRepository as repo
    from app.control.events import emit
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
    from app.world.repository import WorldModelRepository as repo
    from app.control.events import emit
    ok = repo.delete_entity("worldfact", fact_id)
    if ok:
        emit("world.update", {"entity_type": "fact", "action": "delete", "id": fact_id})
    return {"ok": ok}


@router.put("/world-model/routine", dependencies=_auth)
async def admin_upsert_routine(body: _RoutineBody) -> dict[str, Any]:
    from app.world.repository import WorldModelRepository as repo
    from app.control.events import emit
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
    from app.world.repository import WorldModelRepository as repo
    from app.control.events import emit
    hid = _get_household_id()
    if not hid:
        return {"error": "No household found"}
    ok = repo.add_alias(hid, body.entity_type, body.entity_id, body.alias)
    if ok:
        emit("world.update", {"entity_type": body.entity_type, "action": "alias_added", "alias": body.alias})
    return {"ok": ok}


@router.put("/world-model/member-detail", dependencies=_auth)
async def admin_upsert_member_detail(body: _MemberDetailBody) -> dict[str, Any]:
    from app.world.repository import WorldModelRepository as repo
    from app.control.events import emit
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
    from app.world.repository import WorldModelRepository as repo
    from app.control.events import emit
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

    emit("world.update", {"action": "proposal_reviewed", "proposal_id": proposal_id, "decision": body.decision})
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
        emit("world.update", {"action": "bulk_review", "count": reviewed, "decision": body.decision})
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
        steps = sorted(steps_by_task.get(t.id, []), key=lambda s: s.step_index)
        links = links_by_task.get(t.id, [])
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
.wm-del { background:none;border:none;color:#e55;cursor:pointer;font-size:14px;padding:0 4px;opacity:0.5; }
.wm-del:hover { opacity:1; }
.wm-add-link { color:var(--accent);cursor:pointer;font-size:11px;margin-left:6px;text-decoration:none; }
.wm-add-link:hover { text-decoration:underline; }
.proposal-card { background:var(--surface);border:1px solid var(--border);border-radius:6px;padding:10px 12px;margin-bottom:8px;font-size:12px; }
.proposal-card .pc-head { display:flex;align-items:center;gap:8px;margin-bottom:4px; }
.proposal-card .pc-type { background:var(--accent);color:#fff;padding:1px 6px;border-radius:3px;font-size:10px;font-weight:600;text-transform:uppercase; }
.proposal-card .pc-conf { color:var(--dim);font-size:10px; }
.proposal-card .pc-reason { color:var(--text);margin-bottom:6px; }
.proposal-card .pc-payload { color:var(--dim);font-size:11px;font-family:monospace;word-break:break-all; }
.proposal-card .pc-actions { margin-top:6px;display:flex;gap:6px; }
.proposal-card .pc-actions button { border:none;padding:3px 10px;border-radius:4px;cursor:pointer;font-size:11px; }
.pc-accept { background:#238636;color:#fff; }
.pc-accept:hover { background:#2ea043; }
.pc-reject { background:#da3633;color:#fff; }
.pc-reject:hover { background:#e5534b; }
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
  <button class="tab" data-tab="world">World Model</button>
  <button class="tab" data-tab="tasks">Tasks</button>
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
        <thead><tr><th style="width:160px">Name</th><th style="width:100px">Kind</th><th style="width:140px">Schedule</th><th style="width:60px">Status</th><th style="width:110px">Last Fired</th><th style="width:72px">ID</th></tr></thead>
        <tbody id="sched-prompts"><tr><td colspan="6" style="color:var(--dim);padding:12px 10px">—</td></tr></tbody>
      </table>
      <div id="sched-prompt-detail" style="display:none;margin-top:8px;padding:10px 12px;background:var(--card);border:1px solid var(--border);border-radius:6px">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px">
          <strong id="spd-name"></strong>
          <div>
            <button class="details-btn" style="font-size:11px" id="spd-run-now">Run Now</button>
            <button class="details-btn" style="font-size:11px" id="spd-close">Close</button>
          </div>
        </div>
        <div style="font-size:12px;color:var(--dim);margin-bottom:6px">
          <span id="spd-goal"></span>
          <span id="spd-links"></span>
        </div>
        <div id="spd-preview" style="font-size:12px;color:var(--dim);margin-bottom:8px;white-space:pre-wrap;max-height:80px;overflow:auto"></div>
        <h4 style="margin:0 0 4px;font-size:12px">Run History</h4>
        <table class="mem-table" style="font-size:11px">
          <thead><tr><th>Fired</th><th>Status</th><th>Reason</th><th>Preview</th></tr></thead>
          <tbody id="spd-runs"><tr><td colspan="4" style="color:var(--dim);padding:6px">Loading…</td></tr></tbody>
        </table>
      </div>
    </section>
  </div>
</div>

<div id="tab-world" class="tab-panel">
  <div class="details-body">
    <div class="details-toolbar">
      <button class="details-btn" id="refresh-world">&#8635; Refresh</button>
      <span id="world-updated" style="font-size:11px;color:var(--dim)"></span>
    </div>
    <section class="details-section" id="proposals-section" style="display:none">
      <h3>Pending Proposals <span id="wm-proposal-count" class="scope-tag" style="font-size:10px"></span></h3>
      <div id="wm-proposals"></div>
      <div style="margin-top:6px">
        <button id="accept-all-confident" class="details-btn" style="font-size:11px" onclick="wmBulkAcceptConfident()">Accept all high-confidence</button>
      </div>
    </section>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:0 24px">
      <section class="details-section">
        <h3>Members <span id="wm-member-count" style="font-size:10px;color:var(--dim)"></span></h3>
        <div id="wm-members"><span style="color:var(--dim)">—</span></div>
        <div style="margin-top:4px"><a class="wm-add-link" onclick="wmAddMember()">+ member</a></div>
      </section>
      <section class="details-section">
        <h3>Places</h3>
        <div id="wm-places"><span style="color:var(--dim)">—</span></div>
      </section>
    </div>
    <section class="details-section">
      <h3>Devices <span id="wm-device-count" style="font-size:10px;color:var(--dim)"></span></h3>
      <table class="mem-table">
        <thead><tr><th>Name</th><th>Type</th><th>Place</th><th style="width:80px">Controllable</th></tr></thead>
        <tbody id="wm-devices"><tr><td colspan="4" style="color:var(--dim);padding:12px 10px">—</td></tr></tbody>
      </table>
    </section>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:0 24px">
      <section class="details-section">
        <h3>Calendars</h3>
        <table class="mem-table">
          <thead><tr><th>Name</th><th>Category</th><th>Owner</th></tr></thead>
          <tbody id="wm-calendars"><tr><td colspan="3" style="color:var(--dim);padding:12px 10px">—</td></tr></tbody>
        </table>
      </section>
      <section class="details-section">
        <h3>Routines</h3>
        <table class="mem-table">
          <thead><tr><th>Name</th><th>Description</th><th>Kind</th><th style="width:36px"></th></tr></thead>
          <tbody id="wm-routines"><tr><td colspan="4" style="color:var(--dim);padding:12px 10px">—</td></tr></tbody>
          <tfoot><tr>
            <td><input id="wm-add-routine-name" placeholder="name" style="width:100%;background:var(--bg);color:var(--fg);border:1px solid var(--border);padding:2px 4px;font-size:12px"></td>
            <td><input id="wm-add-routine-desc" placeholder="description" style="width:100%;background:var(--bg);color:var(--fg);border:1px solid var(--border);padding:2px 4px;font-size:12px"></td>
            <td><input id="wm-add-routine-kind" placeholder="kind" style="width:100%;background:var(--bg);color:var(--fg);border:1px solid var(--border);padding:2px 4px;font-size:12px"></td>
            <td><button onclick="wmAddRoutine()" style="background:none;border:none;color:var(--accent);cursor:pointer;font-size:14px" title="Add routine">+</button></td>
          </tr></tfoot>
        </table>
      </section>
    </div>
    <section class="details-section">
      <h3>Facts</h3>
      <table class="mem-table">
        <thead><tr><th style="width:120px">Scope</th><th style="width:200px">Key</th><th>Value</th><th style="width:100px">Source</th><th style="width:36px"></th></tr></thead>
        <tbody id="wm-facts"><tr><td colspan="5" style="color:var(--dim);padding:12px 10px">—</td></tr></tbody>
        <tfoot><tr>
          <td><input id="wm-add-fact-scope" placeholder="scope" value="household" style="width:100%;background:var(--bg);color:var(--fg);border:1px solid var(--border);padding:2px 4px;font-size:12px"></td>
          <td><input id="wm-add-fact-key" placeholder="key" style="width:100%;background:var(--bg);color:var(--fg);border:1px solid var(--border);padding:2px 4px;font-size:12px"></td>
          <td><input id="wm-add-fact-value" placeholder="value" style="width:100%;background:var(--bg);color:var(--fg);border:1px solid var(--border);padding:2px 4px;font-size:12px"></td>
          <td></td>
          <td><button onclick="wmAddFact()" style="background:none;border:none;color:var(--accent);cursor:pointer;font-size:14px" title="Add fact">+</button></td>
        </tr></tfoot>
      </table>
    </section>
  </div>
</div>

<div id="tab-tasks" class="tab-panel">
  <div class="details-body">
    <div class="details-toolbar">
      <button class="details-btn" id="refresh-tasks">&#8635; Refresh</button>
      <span id="tasks-updated" style="font-size:11px;color:var(--dim)"></span>
    </div>
    <section class="details-section">
      <h3>Active Tasks <span id="tasks-active-count" style="font-size:10px;color:var(--dim)"></span></h3>
      <table class="mem-table">
        <thead><tr><th>Title</th><th style="width:70px">Kind</th><th style="width:90px">Status</th><th>Summary</th><th style="width:80px">User</th><th style="width:130px">Updated</th><th style="width:80px"></th></tr></thead>
        <tbody id="tasks-table-body"><tr><td colspan="7" style="color:var(--dim);padding:12px 10px">Loading...</td></tr></tbody>
      </table>
    </section>
    <section class="details-section" id="task-detail-section" style="display:none">
      <h3>Task Detail</h3>
      <div id="task-detail-content"></div>
    </section>
  </div>
</div>

<script>
// Token management: strip ?token= from the address bar on first load so it
// never enters browser history or bookmarks. Store in sessionStorage and use
// Bearer header for all fetch() calls. EventSource still uses ?token= since
// browsers cannot send custom headers for SSE connections.
(function() {
  const params = new URLSearchParams(window.location.search);
  const urlToken = params.get('token');
  if (urlToken) {
    sessionStorage.setItem('admin_token', urlToken);
    params.delete('token');
    const clean = window.location.pathname + (params.toString() ? '?' + params.toString() : '');
    history.replaceState(null, '', clean);
  }
})();
const _tok = sessionStorage.getItem('admin_token') || '';
const _authHeaders = _tok ? {'Authorization': 'Bearer ' + _tok} : {};
const _authQ = _tok ? '?token=' + encodeURIComponent(_tok) : '';  // SSE only

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
    'world.update': ['b-mem','WORLD'],
    'world.proposal':['b-mem','WORLD'],
    'task.create':  ['b-sched','TASK'],
    'task.update':  ['b-sched','TASK'],
    'task.await_input':['b-sched','TASK'],
    'task.complete':['b-done','TASK'],
    'task.cancel':  ['b-error','TASK'],
    'task.link':    ['b-sched','TASK'],
    'task.schedule_resume':['b-sched','TASK'],
    'proactive.fire':    ['b-sched','PROACTIVE'],
    'proactive.deliver': ['b-done','PROACTIVE'],
    'proactive.skip':    ['b-mem','PROACTIVE'],
    'proactive.fail':    ['b-error','PROACTIVE'],
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
  } else if (type === 'world.update') {
    body = '<strong>' + (data.action || 'update') + '</strong>'
      + (data.entity_type ? ' <span class="d">' + data.entity_type + '</span>' : '');
  } else if (type === 'world.proposal') {
    const st = data.status === 'auto_applied' ? '<span style="color:var(--green)">auto-applied</span>' : '<span class="d">pending</span>';
    body = '<strong>' + (data.type || 'proposal') + '</strong> ' + st
      + ' <span class="d">' + Math.round((data.confidence || 0) * 100) + '% — ' + (data.reason || '') + '</span>';
    loadProposals();
  } else if (type.startsWith('task.')) {
    const action = type.split('.')[1] || '';
    const title = data.title || data.summary || data.prompt_hint || data.reason || '';
    body = '<strong>' + action + '</strong>'
      + (title ? ' <span class="d">— ' + title.slice(0, 100) + '</span>' : '');
    loadTasks();
  } else if (type.startsWith('proactive.')) {
    const action = type.split('.')[1] || '';
    const pName = data.name || '';
    const kind = data.behavior_kind ? ' <span class="d">[' + data.behavior_kind + ']</span>' : '';
    const reason = data.reason ? ' <span class="d">— ' + data.reason + '</span>' : '';
    const dur = data.duration_ms ? ' <span class="d">' + data.duration_ms + 'ms</span>' : '';
    body = '<strong>' + action + '</strong> ' + pName + kind + reason + dur;
    loadScheduler();
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
    const r = await fetch('/admin/stats', {headers: _authHeaders});
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
    if (btn.dataset.tab === 'world') { loadWorldModel(); loadProposals(); }
    if (btn.dataset.tab === 'tasks') loadTasks();
  });
});

// Memory details loader
async function loadMemory() {
  try {
    const r = await fetch('/admin/memory', {headers: _authHeaders});
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
    const r = await fetch('/admin/scheduler', {headers: _authHeaders});
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
    window._schedPrompts = sp;
    const statusBadge = s => {
      if (!s) return '<span style="color:var(--dim)">—</span>';
      const colors = {delivered:'var(--green)',skipped:'#c90',failed:'var(--red)'};
      return '<span style="color:' + (colors[s]||'var(--dim)') + '">' + s + '</span>';
    };
    document.getElementById('sched-prompts').innerHTML = sp.length
      ? sp.map(p =>
          '<tr style="cursor:pointer" onclick="showPromptDetail(\\'' + p.id + '\\')">' +
          '<td>' + esc(p.name) + '</td>' +
          '<td class="mem-id">' + esc(p.behavior_kind || 'generic_prompt') + '</td>' +
          '<td class="time-tag">' + esc(p.recurrence) + '</td>' +
          '<td>' + statusBadge(p.last_status) + '</td>' +
          '<td class="time-tag">' + (p.last_fired_at ? fmtTime(p.last_fired_at) : '—') + '</td>' +
          '<td class="mem-id">' + esc(p.id.slice(0,8)) + '</td></tr>'
        ).join('')
      : '<tr><td colspan="6" style="color:var(--dim);padding:12px 10px">No scheduled prompts</td></tr>';
  } catch(e) {}
}

async function showPromptDetail(promptId) {
  const sp = (window._schedPrompts || []).find(p => p.id === promptId);
  if (!sp) return;
  const det = document.getElementById('sched-prompt-detail');
  det.style.display = 'block';
  det.dataset.promptId = promptId;
  document.getElementById('spd-name').textContent = sp.name + ' [' + (sp.behavior_kind || 'generic_prompt') + ']';
  document.getElementById('spd-goal').textContent = sp.goal ? 'Goal: ' + sp.goal : '';
  const links = (sp.linked_entities || []).map(l => l.entity_type + ':' + l.entity_id).join(', ');
  document.getElementById('spd-links').textContent = links ? ' | Linked: ' + links : '';
  document.getElementById('spd-preview').textContent = sp.last_result_preview || '(no result yet)';
  document.getElementById('spd-runs').innerHTML = '<tr><td colspan="4" style="color:var(--dim);padding:6px">Loading…</td></tr>';
  try {
    const r = await fetch('/admin/scheduler/runs/' + promptId, {headers: _authHeaders});
    if (!r.ok) return;
    const d = await r.json();
    const runs = d.runs || [];
    document.getElementById('spd-runs').innerHTML = runs.length
      ? runs.map(r =>
          '<tr><td class="time-tag">' + (r.fired_at ? fmtTime(r.fired_at) : '—') + '</td>' +
          '<td>' + esc(r.status) + '</td>' +
          '<td style="color:var(--dim)">' + esc(r.skip_reason || '') + '</td>' +
          '<td style="color:var(--dim);max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">' + esc(r.output_preview || '').slice(0,80) + '</td></tr>'
        ).join('')
      : '<tr><td colspan="4" style="color:var(--dim);padding:6px">No runs yet</td></tr>';
  } catch(e) {}
}

document.getElementById('spd-close').addEventListener('click', () => {
  document.getElementById('sched-prompt-detail').style.display = 'none';
});
document.getElementById('spd-run-now').addEventListener('click', async () => {
  const id = document.getElementById('sched-prompt-detail').dataset.promptId;
  if (!id) return;
  try {
    await fetch('/admin/scheduler/' + id + '/run-now', {method:'POST', headers: _authHeaders});
  } catch(e) {}
});

document.getElementById('refresh-scheduler').addEventListener('click', loadScheduler);

// World Model tab loader
async function loadWorldModel() {
  try {
    const r = await fetch('/admin/world-model', {headers: _authHeaders});
    if (!r.ok) return;
    const d = await r.json();
    if (d.error) { document.getElementById('wm-members').innerHTML = '<span style="color:var(--dim)">' + esc(d.error) + '</span>'; return; }

    // Build lookup maps
    const memberById = {};
    (d.members || []).forEach(m => { memberById[m.id] = m.name; });
    const placeById = {};
    (d.places || []).forEach(p => { placeById[p.id] = p.name; });

    // Members with interests/activities/goals inline
    const members = d.members || [];
    document.getElementById('wm-member-count').textContent = '(' + members.length + ')';
    const interestsByMember = {};
    (d.interests || []).forEach(i => { (interestsByMember[i.member_id] = interestsByMember[i.member_id] || []).push(i.name); });
    const actsByMember = {};
    (d.activities || []).forEach(a => { const lbl = a.name + (a.schedule_hint ? ' (' + a.schedule_hint + ')' : ''); (actsByMember[a.member_id] = actsByMember[a.member_id] || []).push(lbl); });
    const goalsByMember = {};
    (d.goals || []).forEach(g => { (goalsByMember[g.member_id] = goalsByMember[g.member_id] || []).push(g.name); });

    document.getElementById('wm-members').innerHTML = members.length
      ? members.map(m => {
          let aliases = ''; try { const a = JSON.parse(m.aliases_json || '[]'); if (a.length) aliases = ' <span class="d">(' + a.map(esc).join(', ') + ')</span>'; } catch(_) {}
          let sub = [];
          if (interestsByMember[m.id]) sub.push('interests: ' + interestsByMember[m.id].join(', '));
          if (actsByMember[m.id]) sub.push('activities: ' + actsByMember[m.id].join(', '));
          if (goalsByMember[m.id]) sub.push('goals: ' + goalsByMember[m.id].join(', '));
          const subHtml = sub.length ? '<div style="margin-left:16px;color:var(--dim);font-size:12px">' + sub.map(s => '· ' + esc(s)).join('<br>') + '</div>' : '';
          const addBtns = '<div style="margin-left:16px;margin-top:2px">'
            + '<a class="wm-add-link" onclick="wmAddMemberDetail(\\'interest\\',\\'' + m.id + '\\')">+ interest</a>'
            + '<a class="wm-add-link" onclick="wmAddMemberDetail(\\'activity\\',\\'' + m.id + '\\')">+ activity</a>'
            + '<a class="wm-add-link" onclick="wmAddMemberDetail(\\'goal\\',\\'' + m.id + '\\')">+ goal</a>'
            + '</div>';
          return '<div style="margin-bottom:6px"><strong>' + esc(m.name) + '</strong> <span class="scope-tag">' + esc(m.role) + '</span>' + aliases + subHtml + addBtns + '</div>';
        }).join('')
      : '<span style="color:var(--dim)">No members</span>';

    // Places (hierarchical)
    const places = d.places || [];
    const topLevel = places.filter(p => !p.parent_place_id);
    const children = {};
    places.filter(p => p.parent_place_id).forEach(p => {
      (children[p.parent_place_id] = children[p.parent_place_id] || []).push(p);
    });
    document.getElementById('wm-places').innerHTML = topLevel.length
      ? topLevel.map(p => {
          const kids = children[p.id] || [];
          const kidNames = kids.map(k => esc(k.name)).join(', ');
          return '<div style="margin-bottom:4px"><strong>' + esc(p.name) + '</strong> <span class="scope-tag">' + esc(p.kind) + '</span>'
            + (kidNames ? '<div style="margin-left:16px;color:var(--dim);font-size:12px">' + kidNames + '</div>' : '')
            + '</div>';
        }).join('')
      : '<span style="color:var(--dim)">No places</span>';

    // Devices grouped by place
    const devices = d.devices || [];
    document.getElementById('wm-device-count').textContent = '(' + devices.length + ')';
    document.getElementById('wm-devices').innerHTML = devices.length
      ? devices.map(dev =>
          '<tr><td>' + esc(dev.name) + '</td>' +
          '<td class="scope-tag">' + esc(dev.device_type || '—') + '</td>' +
          '<td class="scope-tag">' + esc(dev.place_id ? (placeById[dev.place_id] || dev.place_id.slice(0,8)) : '—') + '</td>' +
          '<td class="scope-tag">' + (dev.is_controllable ? '✓' : '—') + '</td></tr>'
        ).join('')
      : '<tr><td colspan="4" style="color:var(--dim);padding:12px 10px">No devices</td></tr>';

    // Calendars
    const cals = d.calendars || [];
    document.getElementById('wm-calendars').innerHTML = cals.length
      ? cals.map(c =>
          '<tr><td>' + esc(c.name) + '</td>' +
          '<td class="scope-tag">' + esc(c.category || 'general') + '</td>' +
          '<td class="scope-tag">' + esc(c.member_id ? (memberById[c.member_id] || '—') : '—') + '</td></tr>'
        ).join('')
      : '<tr><td colspan="3" style="color:var(--dim);padding:12px 10px">No calendars</td></tr>';

    // Routines
    const routines = d.routines || [];
    document.getElementById('wm-routines').innerHTML = routines.length
      ? routines.map(r =>
          '<tr><td>' + esc(r.name) + '</td>' +
          '<td style="color:var(--dim)">' + esc(r.description || '—') + '</td>' +
          '<td class="scope-tag">' + esc(r.kind || '—') + '</td>' +
          '<td><button onclick="wmDeleteEntity(\\'routineentity\\',\\'' + esc(r.id) + '\\')" class="wm-del" title="Delete">×</button></td></tr>'
        ).join('')
      : '<tr><td colspan="4" style="color:var(--dim);padding:12px 10px">No routines</td></tr>';

    // Facts
    const facts = d.facts || [];
    document.getElementById('wm-facts').innerHTML = facts.length
      ? facts.map(f => {
          let val; try { val = JSON.parse(f.value_json); } catch(_) { val = f.value_json; }
          return '<tr><td class="scope-tag">' + esc(f.scope) + '</td>' +
            '<td>' + esc(f.key) + '</td>' +
            '<td>' + esc(String(val)) + '</td>' +
            '<td class="scope-tag">' + esc(f.source || '—') + '</td>' +
            '<td><button onclick="wmDeleteEntity(\\'worldfact\\',\\'' + esc(f.id) + '\\')" class="wm-del" title="Delete">×</button></td></tr>';
        }).join('')
      : '<tr><td colspan="5" style="color:var(--dim);padding:12px 10px">No facts</td></tr>';

    document.getElementById('world-updated').textContent = 'Updated ' + new Date().toLocaleTimeString();
  } catch(e) {}
}

document.getElementById('refresh-world').addEventListener('click', loadWorldModel);

// World model inline editing helpers
async function wmDeleteEntity(entityType, entityId) {
  if (!confirm('Delete this entry?')) return;
  await fetch('/admin/world-model/entity/' + entityType + '/' + entityId, {method:'DELETE', headers:_authHeaders});
  loadWorldModel();
}
async function wmAddFact() {
  const scope = document.getElementById('wm-add-fact-scope').value.trim();
  const key = document.getElementById('wm-add-fact-key').value.trim();
  const value = document.getElementById('wm-add-fact-value').value.trim();
  if (!key) return;
  await fetch('/admin/world-model/fact', {method:'PUT', headers:{..._authHeaders,'Content-Type':'application/json'}, body:JSON.stringify({scope:scope||'household',key,value})});
  document.getElementById('wm-add-fact-key').value = '';
  document.getElementById('wm-add-fact-value').value = '';
  loadWorldModel();
}
async function wmAddRoutine() {
  const name = document.getElementById('wm-add-routine-name').value.trim();
  const description = document.getElementById('wm-add-routine-desc').value.trim();
  const kind = document.getElementById('wm-add-routine-kind').value.trim();
  if (!name) return;
  await fetch('/admin/world-model/routine', {method:'PUT', headers:{..._authHeaders,'Content-Type':'application/json'}, body:JSON.stringify({name,description,kind})});
  document.getElementById('wm-add-routine-name').value = '';
  document.getElementById('wm-add-routine-desc').value = '';
  document.getElementById('wm-add-routine-kind').value = '';
  loadWorldModel();
}
async function wmAddMember() {
  const name = prompt('Member name:');
  if (!name) return;
  const role = prompt('Role (member, child, guest):', 'member');
  if (!role) return;
  await fetch('/admin/world-model/member', {method:'PUT', headers:{..._authHeaders,'Content-Type':'application/json'}, body:JSON.stringify({name, role})});
  loadWorldModel();
}
async function wmAddMemberDetail(detailType, memberId) {
  const name = prompt(detailType.charAt(0).toUpperCase() + detailType.slice(1) + ' name:');
  if (!name) return;
  await fetch('/admin/world-model/member-detail', {method:'PUT', headers:{..._authHeaders,'Content-Type':'application/json'}, body:JSON.stringify({detail_type:detailType,member_id:memberId,name})});
  loadWorldModel();
}
async function wmDeleteMemberDetail(detailType, entityId) {
  if (!confirm('Delete this ' + detailType + '?')) return;
  await fetch('/admin/world-model/entity/' + 'member' + detailType + '/' + entityId, {method:'DELETE', headers:_authHeaders});
  loadWorldModel();
}

// Proposals
async function loadProposals() {
  try {
    const r = await fetch('/admin/world-model/proposals', {headers: _authHeaders});
    if (!r.ok) return;
    const d = await r.json();
    const sec = document.getElementById('proposals-section');
    const cnt = document.getElementById('wm-proposal-count');
    const container = document.getElementById('wm-proposals');
    const pending = (d.proposals || []).filter(p => p.status === 'pending');
    if (!pending.length) { sec.style.display = 'none'; return; }
    sec.style.display = '';
    cnt.textContent = pending.length;
    container.innerHTML = pending.map(p => {
      const confPct = Math.round(p.confidence * 100);
      const payloadStr = JSON.stringify(p.payload, null, 1);
      return '<div class="proposal-card" id="prop-' + esc(p.id) + '">'
        + '<div class="pc-head"><span class="pc-type">' + esc(p.proposal_type) + '</span>'
        + '<span class="pc-conf">' + confPct + '% confidence</span></div>'
        + '<div class="pc-reason">' + esc(p.reason) + '</div>'
        + '<div class="pc-payload">' + esc(payloadStr) + '</div>'
        + '<div class="pc-actions">'
        + '<button class="pc-accept" onclick="wmReviewProposal(\\'' + esc(p.id) + '\\',\\'accepted\\')">Accept</button>'
        + '<button class="pc-reject" onclick="wmReviewProposal(\\'' + esc(p.id) + '\\',\\'rejected\\')">Reject</button>'
        + '</div></div>';
    }).join('');
  } catch(e) {}
}
async function wmReviewProposal(id, decision) {
  await fetch('/admin/world-model/proposals/' + id + '/review', {
    method:'POST', headers:{..._authHeaders,'Content-Type':'application/json'},
    body: JSON.stringify({decision})
  });
  const card = document.getElementById('prop-' + id);
  if (card) card.style.display = 'none';
  loadProposals();
  if (decision === 'accepted') loadWorldModel();
}
async function wmBulkAcceptConfident() {
  try {
    const r = await fetch('/admin/world-model/proposals', {headers: _authHeaders});
    if (!r.ok) return;
    const d = await r.json();
    const ids = (d.proposals || []).filter(p => p.status === 'pending' && p.confidence >= 0.7).map(p => p.id);
    if (!ids.length) return;
    await fetch('/admin/world-model/proposals/bulk', {
      method:'POST', headers:{..._authHeaders,'Content-Type':'application/json'},
      body: JSON.stringify({proposal_ids: ids, decision: 'accepted'})
    });
    loadProposals();
    loadWorldModel();
  } catch(e) {}
}

// --- Tasks tab ---
async function loadTasks() {
  try {
    const r = await fetch('/admin/tasks', {headers: _authHeaders});
    if (!r.ok) return;
    const d = await r.json();
    const tasks = d.tasks || [];
    const active = tasks.filter(t => t.status !== 'COMPLETED' && t.status !== 'FAILED' && t.status !== 'CANCELLED');
    document.getElementById('tasks-active-count').textContent = active.length ? '(' + active.length + ')' : '';
    document.getElementById('tasks-updated').textContent = 'Updated ' + new Date().toLocaleTimeString();
    const tbody = document.getElementById('tasks-table-body');
    if (!tasks.length) {
      tbody.innerHTML = '<tr><td colspan="7" style="color:var(--dim);padding:12px 10px">No tasks</td></tr>';
      return;
    }
    const statusColor = {ACTIVE:'var(--green)',AWAITING_INPUT:'var(--yellow)',AWAITING_CONFIRMATION:'var(--yellow)',COMPLETED:'var(--dim)',FAILED:'var(--red)',CANCELLED:'var(--dim)'};
    tbody.innerHTML = tasks.map(function(t) {
      const sc = statusColor[t.status] || 'var(--dim)';
      const updated = t.updated_at ? new Date(t.updated_at).toLocaleString() : '';
      const actions = (t.status === 'ACTIVE' || t.status === 'AWAITING_INPUT' || t.status === 'AWAITING_CONFIRMATION')
        ? '<button onclick="taskAction(\\'' + esc(t.id) + '\\',\\'cancel\\')" style="background:none;border:none;color:var(--red);cursor:pointer;font-size:11px">Cancel</button>'
          + (t.status !== 'ACTIVE' ? ' <button onclick="taskAction(\\'' + esc(t.id) + '\\',\\'resume\\')" style="background:none;border:none;color:var(--green);cursor:pointer;font-size:11px">Resume</button>' : '')
        : '';
      return '<tr onclick="showTaskDetail(\\'' + esc(t.id) + '\\')" style="cursor:pointer">'
        + '<td>' + esc(t.title || '') + '</td>'
        + '<td><span class="scope-tag">' + esc(t.task_kind || '') + '</span></td>'
        + '<td><span style="color:' + sc + '">' + esc(t.status) + '</span></td>'
        + '<td style="color:var(--dim);font-size:12px">' + esc(t.summary || '') + '</td>'
        + '<td>' + esc(t.user || '') + '</td>'
        + '<td style="font-size:11px;color:var(--dim)">' + esc(updated) + '</td>'
        + '<td>' + actions + '</td></tr>';
    }).join('');
  } catch(e) {}
}
var _tasksData = {};
async function showTaskDetail(taskId) {
  try {
    const r = await fetch('/admin/tasks', {headers: _authHeaders});
    if (!r.ok) return;
    const d = await r.json();
    const t = (d.tasks || []).find(function(x) { return x.id === taskId; });
    if (!t) return;
    document.getElementById('task-detail-section').style.display = '';
    var html = '<div style="margin-bottom:12px"><strong>' + esc(t.title) + '</strong>'
      + ' <span class="scope-tag">' + esc(t.task_kind || '') + '</span>'
      + ' <span style="font-size:11px;color:var(--dim)">ID: ' + esc(t.id.slice(0,8)) + '</span></div>';
    if (t.summary) html += '<div style="margin-bottom:8px">Summary: ' + esc(t.summary) + '</div>';
    if (t.awaiting_input_hint) html += '<div style="margin-bottom:8px;color:var(--yellow)">Waiting for: ' + esc(t.awaiting_input_hint) + '</div>';
    if (t.steps && t.steps.length) {
      html += '<div style="margin-bottom:8px"><strong>Steps:</strong></div><div style="margin-left:12px">';
      t.steps.forEach(function(s) {
        var icon = {done:'[x]', active:'[>]', failed:'[!]', cancelled:'[-]'}[s.status] || '[ ]';
        html += '<div style="font-size:12px;margin-bottom:2px"><code>' + icon + '</code> ' + esc(s.title) + ' <span style="color:var(--dim)">(' + esc(s.type) + ')</span></div>';
      });
      html += '</div>';
    }
    if (t.links && t.links.length) {
      html += '<div style="margin-top:8px"><strong>Linked entities:</strong></div><div style="margin-left:12px">';
      t.links.forEach(function(ln) {
        html += '<div style="font-size:12px;margin-bottom:2px">' + esc(ln.entity_type) + ': ' + esc(ln.entity_id) + ' (' + esc(ln.role) + ')</div>';
      });
      html += '</div>';
    }
    document.getElementById('task-detail-content').innerHTML = html;
  } catch(e) {}
}
async function taskAction(taskId, action) {
  try {
    await fetch('/admin/tasks/' + taskId + '/action', {
      method:'POST', headers:{..._authHeaders,'Content-Type':'application/json'},
      body: JSON.stringify({action: action})
    });
    loadTasks();
  } catch(e) {}
}
document.getElementById('refresh-tasks').addEventListener('click', loadTasks);

// Stream via fetch + ReadableStream (replaces EventSource which breaks in Safari)
async function connectSSE() {
  const pulse = document.getElementById('pulse');
  const label = document.getElementById('conn-label');
  try {
    const resp = await fetch('/admin/stream', {headers: _authHeaders});
    if (resp.status === 401) {
      pulse.className = 'pulse off';
      label.className = 'err';
      label.textContent = 'Auth required';
      document.querySelector('.tab-panel.active').innerHTML =
        '<div style="padding:40px 20px;color:var(--dim);text-align:center">' +
        '<p style="font-size:15px;margin-bottom:8px">Authentication required</p>' +
        '<p style="font-size:12px">Access with <code>?token=APP_SECRET_KEY</code> in the URL</p></div>';
      return; // don't retry
    }
    if (!resp.ok) throw new Error('HTTP ' + resp.status);
    pulse.className = 'pulse';
    label.className = 'ok';
    label.textContent = 'Connected';
    const reader = resp.body.getReader();
    const dec = new TextDecoder();
    let buf = '';
    while (true) {
      const {done, value} = await reader.read();
      if (done) break;
      buf += dec.decode(value, {stream: true});
      const parts = buf.split('\\n\\n');
      buf = parts.pop();
      for (const part of parts) {
        if (!part.trim() || part.startsWith(':')) continue;
        let evType = 'message', data = '';
        for (const line of part.split('\\n')) {
          if (line.startsWith('event: ')) evType = line.slice(7);
          else if (line.startsWith('data: ')) data = line.slice(6);
        }
        if (data) { try { addEvent(evType, JSON.parse(data)); } catch(_) {} }
      }
    }
  } catch (_) {
    pulse.className = 'pulse off';
    label.className = 'err';
    label.textContent = 'Reconnecting…';
  }
  setTimeout(connectSSE, 3000);
}

fetchStats();
setInterval(fetchStats, 10000);
connectSSE();
</script>
</body>
</html>"""
