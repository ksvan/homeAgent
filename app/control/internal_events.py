"""
Internal event helpers — Phase 3b.

Provides helpers for emitting internal InboundEvents back into the event bus
so that system-generated signals (e.g. verify_result) can be routed through
the same dispatcher path as external events.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def emit_verify_result(
    *,
    household_id: str,
    device_id: str,
    capability: str,
    expected: object,
    observed: str,
    ok: bool,
    control_task_id: str | None = None,
) -> None:
    """
    Enqueue an internal verify_result InboundEvent on the event bus.

    Called from verify_after_write() after reading back device state.
    The dispatcher will route it to any matching EventRule (typically a
    task_loop rule whose control_task_id matches an active control task).
    """
    try:
        from app.control.event_bus import InboundEvent, enqueue_event

        payload: dict = {
            "capability": capability,
            "expected": expected,
            "observed": observed,
            "ok": ok,
        }
        if control_task_id:
            payload["control_task_id"] = control_task_id

        event = InboundEvent(
            source="internal",
            event_type="verify_result",
            household_id=household_id,
            entity_id=device_id,
            payload=payload,
            timestamp=datetime.now(timezone.utc),
            raw={},
        )
        enqueue_event(event)
        logger.debug(
            "verify_result emitted device=%s capability=%s ok=%s task=%s",
            device_id,
            capability,
            ok,
            control_task_id or "none",
        )
    except Exception:
        logger.warning("Failed to emit verify_result internal event", exc_info=True)
