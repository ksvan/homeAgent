"""
Control loop service — Phase 3a.

Resolves or creates a durable control Task for task_loop-mode EventRules.
Called by the dispatcher after a rule match; keeps subsequent events for the
same issue correlated to one task rather than spawning isolated runs.
"""

from __future__ import annotations

import json
import logging

from app.control.event_bus import InboundEvent

logger = logging.getLogger(__name__)


def resolve_or_create_control_task(event: InboundEvent, rule: object) -> str:
    """
    Find an active Task whose control correlation key matches this event+rule,
    or create a new one.  Returns the task ID.
    """
    from app.control.events import emit
    from app.tasks.repository import TaskRepository

    correlation_key = _compute_correlation_key(event, rule)
    repo = TaskRepository()

    existing = repo.find_active_by_correlation_key(event.household_id, correlation_key)
    if existing:
        _merge_event(repo, existing, event, correlation_key)
        emit(
            "control.task_reused",
            {"task_id": existing.id, "correlation_key": correlation_key},
        )
        logger.debug("Control task reused: %s (%s)", existing.id, correlation_key)
        return existing.id

    # No matching task — create one
    task_kind: str = getattr(rule, "task_kind_default", None) or "track"
    entity_name: str = event.payload.get("entity_name", event.entity_id)
    rule_name: str = getattr(rule, "name", "event")

    ctx: dict = {
        "control": {
            "rule_id": getattr(rule, "id", ""),
            "run_mode": "task_loop",
            "phase": "OBSERVE",
            "correlation_key": correlation_key,
            "last_event": event.payload,
            "expected_effect": None,
            "waiting_reason": None,
            "verify_pending": False,
        }
    }

    task = repo.create_task(
        household_id=event.household_id,
        user_id=getattr(rule, "user_id", ""),
        title=f"[{rule_name}] {entity_name}",
        task_kind=task_kind,
        context=ctx,
    )
    repo.add_link(task.id, "event_rule", getattr(rule, "id", ""), role="source")
    if event.entity_id and event.entity_id != "*":
        repo.add_link(task.id, "device", event.entity_id, role="subject")

    emit(
        "control.task_created",
        {
            "task_id": task.id,
            "rule_id": getattr(rule, "id", ""),
            "correlation_key": correlation_key,
        },
    )
    logger.info("Control task created: %s (%s)", task.id, correlation_key)
    return task.id


def _compute_correlation_key(event: InboundEvent, rule: object) -> str:
    tpl: str = (
        getattr(rule, "correlation_key_tpl", None)
        or "rule:{rule_id}:entity:{entity_id}"
    )
    return tpl.format(rule_id=getattr(rule, "id", ""), entity_id=event.entity_id)


def _merge_event(
    repo: object,
    task: object,
    event: InboundEvent,
    correlation_key: str,
) -> None:
    """Update the task's control context with the latest event payload."""
    from app.tasks.repository import TaskRepository

    if not isinstance(repo, TaskRepository):
        return

    try:
        ctx = json.loads(getattr(task, "context", "{}") or "{}")
    except (json.JSONDecodeError, AttributeError):
        ctx = {}

    ctrl: dict = ctx.setdefault("control", {})
    ctrl["last_event"] = event.payload
    ctrl["phase"] = "OBSERVE"
    ctrl["correlation_key"] = correlation_key

    repo.update_task(getattr(task, "id", ""), context=json.dumps(ctx))
