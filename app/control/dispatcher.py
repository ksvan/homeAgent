"""
Event dispatcher.

run_event_dispatcher() is a long-running background task started in _lifespan.

For each InboundEvent it:
  1. Syncs world state cache (always, fast, inline)
  2. Evaluates enabled EventRule records for this household
  3. Applies cooldown / quiet-hours filtering
  4. If a rule matches, builds a structured prompt envelope and fires agent_run()
     as a background asyncio.Task (non-blocking — dispatch loop keeps draining)

Locking: each spawned agent task acquires get_user_run_lock(rule.user_id) before
calling agent_run(), preserving the same per-user serialisation contract as all
other triggers.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone

from app.control.event_bus import InboundEvent, get_event

logger = logging.getLogger(__name__)

# In-memory cooldown tracker: {rule_id: last_triggered_at}
_rule_last_triggered: dict[str, datetime] = {}


async def run_event_dispatcher() -> None:
    """
    Background loop: dequeue InboundEvents and route them.
    Runs for the lifetime of the process; cancelled on shutdown.
    """
    logger.info("Event dispatcher started")
    while True:
        event = await get_event()
        try:
            await _dispatch(event)
        except Exception:
            logger.exception(
                "Event dispatch error source=%s type=%s entity=%s",
                event.source,
                event.event_type,
                event.entity_id,
            )


async def _dispatch(event: InboundEvent) -> None:
    from app.control.events import emit

    emit("event.received", {
        "source": event.source,
        "event_type": event.event_type,
        "entity_id": event.entity_id,
        "household_id": event.household_id,
    })

    # 1. Always sync world state (fast, inline — never blocked by agent runs)
    await _sync_world_state(event)

    emit("event.synced", {
        "source": event.source,
        "event_type": event.event_type,
        "entity_id": event.entity_id,
    })

    # 2. Find matching EventRule records
    rules = await _load_matching_rules(event)
    if not rules:
        return

    for rule in rules:
        # 3. Cooldown check
        if _is_on_cooldown(rule):
            logger.debug("Rule %s suppressed by cooldown", rule.id)
            emit("event.suppressed", {"rule_id": rule.id, "reason": "cooldown"})
            continue

        # 4. Quiet-hours / condition check
        if _in_quiet_hours(rule):
            logger.debug("Rule %s suppressed by quiet hours", rule.id)
            emit("event.suppressed", {"rule_id": rule.id, "reason": "quiet_hours"})
            continue

        # 5. Value filter check
        if not _matches_value_filter(event, rule):
            logger.debug("Rule %s suppressed by value filter", rule.id)
            emit("event.suppressed", {"rule_id": rule.id, "reason": "value_filter"})
            continue

        # Update cooldown timestamp before spawning so rapid re-fires are
        # suppressed even if the agent task takes a while to start.
        _rule_last_triggered[rule.id] = datetime.now(timezone.utc)

        text = _build_prompt_envelope(event, rule)
        emit("event.triggered", {
            "rule_id": rule.id,
            "rule_name": rule.name,
            "entity_id": event.entity_id,
        })

        # 6. Fire agent_run() as a background task so the dispatch loop
        #    continues draining events immediately.
        _schedule_agent_run(text, rule, event)


def _schedule_agent_run(text: str, rule: object, event: InboundEvent) -> None:
    """Spawn a background task that acquires the per-user lock and calls agent_run()."""
    from app.agent.runner import agent_run, get_user_run_lock

    async def _run() -> None:
        async with get_user_run_lock(rule.user_id):  # type: ignore[attr-defined]
            await agent_run(
                text=text,
                user_id=rule.user_id,  # type: ignore[attr-defined]
                household_id=event.household_id,
                channel_user_id=rule.channel_user_id,  # type: ignore[attr-defined]
                trigger="event",
                save_history=False,
            )

    def _on_done(fut: asyncio.Future) -> None:  # type: ignore[type-arg]
        if not fut.cancelled() and (exc := fut.exception()):
            logger.error(
                "Event-triggered agent_run failed rule=%s: %s",
                rule.name,  # type: ignore[attr-defined]
                exc,
                exc_info=exc,
            )

    asyncio.create_task(_run()).add_done_callback(_on_done)


async def _sync_world_state(event: InboundEvent) -> None:
    """Update the device state cache from the inbound event (lightweight path)."""
    if event.event_type != "device_state_change":
        return
    try:
        from app.homey.state_cache import upsert_snapshot

        capability = str(event.payload.get("capability", ""))
        value = str(event.payload.get("value", ""))
        if capability:
            upsert_snapshot(
                event.household_id,
                event.entity_id,
                capability,
                value,
                source="event",
            )
    except Exception:
        logger.warning("Failed to sync world state for event", exc_info=True)


async def _load_matching_rules(event: InboundEvent) -> list:
    """Load enabled EventRules for this household that match the event."""
    try:
        from sqlmodel import select

        from app.db import users_session
        from app.models.events import EventRule

        with users_session() as session:
            rules = session.exec(
                select(EventRule).where(
                    EventRule.household_id == event.household_id,
                    EventRule.enabled == True,  # noqa: E712
                )
            ).all()

        matched = []
        for rule in rules:
            if rule.source not in (event.source, "*"):
                continue
            if rule.event_type not in (event.event_type, "*"):
                continue
            if rule.entity_id not in (event.entity_id, "*"):
                continue
            if rule.capability is not None:
                if rule.capability != event.payload.get("capability"):
                    continue
            matched.append(rule)
        return matched
    except Exception:
        logger.warning("Failed to load event rules", exc_info=True)
        return []


def _is_on_cooldown(rule: object) -> bool:
    last = _rule_last_triggered.get(rule.id)  # type: ignore[attr-defined]
    if last is None:
        return False
    elapsed = datetime.now(timezone.utc) - last
    return elapsed < timedelta(minutes=rule.cooldown_minutes)  # type: ignore[attr-defined]


def _in_quiet_hours(rule: object) -> bool:
    """Return True if the current time falls within the rule's quiet hours."""
    condition_json = rule.condition_json  # type: ignore[attr-defined]
    if not condition_json:
        return False
    try:
        cond = json.loads(condition_json)
    except (json.JSONDecodeError, TypeError):
        return False

    qh_start = cond.get("quiet_hours_start")
    qh_end = cond.get("quiet_hours_end")
    if not qh_start or not qh_end:
        return False

    now_time = datetime.now(timezone.utc).strftime("%H:%M")
    # Simple string comparison works for HH:MM when start < end (same day).
    # For overnight ranges (e.g. 22:00–07:00) we check the complement.
    if qh_start <= qh_end:
        return qh_start <= now_time < qh_end
    else:
        # Overnight: quiet if after start OR before end
        return now_time >= qh_start or now_time < qh_end


def _matches_value_filter(event: InboundEvent, rule: object) -> bool:
    """Return True if the event value satisfies the rule's value_filter_json."""
    filter_json = rule.value_filter_json  # type: ignore[attr-defined]
    if not filter_json:
        return True
    try:
        filt = json.loads(filter_json)
    except (json.JSONDecodeError, TypeError):
        return True

    value = event.payload.get("value")
    if "eq" in filt:
        return value == filt["eq"]
    if "ne" in filt:
        return value != filt["ne"]
    if "gt" in filt:
        return isinstance(value, (int, float)) and value > filt["gt"]
    if "lt" in filt:
        return isinstance(value, (int, float)) and value < filt["lt"]
    if "gte" in filt:
        return isinstance(value, (int, float)) and value >= filt["gte"]
    if "lte" in filt:
        return isinstance(value, (int, float)) and value <= filt["lte"]
    return True


def _build_prompt_envelope(event: InboundEvent, rule: object) -> str:
    """Build the structured text passed to agent_run() for an event-triggered run."""
    now_str = datetime.now(timezone.utc).strftime("%H:%M UTC")
    entity_name = event.payload.get("entity_name", event.entity_id)
    capability = event.payload.get("capability", "")
    value = event.payload.get("value", "")
    zone = event.payload.get("zone", "")

    template: str = rule.prompt_template  # type: ignore[attr-defined]
    task_text = template.format(
        entity_id=event.entity_id,
        entity_name=entity_name,
        capability=capability,
        value=value,
        zone=zone,
        time=now_str,
    )

    zone_line = f"\n- zone: {zone}" if zone else ""
    cap_line = f"\n- capability: {capability} → {value}" if capability else ""

    return (
        f"## Event Trigger\n"
        f"- source: {event.source}\n"
        f"- event_type: {event.event_type}\n"
        f"- entity: {entity_name} ({event.entity_id})"
        f"{cap_line}"
        f"{zone_line}\n"
        f"- time: {now_str}\n\n"
        f"## Rule\n"
        f"{rule.name}\n\n"  # type: ignore[attr-defined]
        f"## Task\n"
        f"{task_text}"
    )
