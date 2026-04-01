from __future__ import annotations

import asyncio
import logging
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

_MAX_RING = 150  # events kept in memory for late-joining SSE clients


@dataclass
class ControlEvent:
    event_type: str  # "run.start" | "run.tool_call" | "run.complete" | "run.error"
    run_id: str
    payload: dict[str, Any]
    ts: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


# Ring buffer of recent events (for late-joining clients)
_ring: deque[ControlEvent] = deque(maxlen=_MAX_RING)

# Active SSE subscriber queues
_subscribers: set[asyncio.Queue[ControlEvent]] = set()


def emit(event_type: str, payload: dict[str, Any], run_id: str = "") -> None:
    """Emit a control event to the ring buffer and all active SSE subscribers."""
    event = ControlEvent(
        event_type=event_type,
        run_id=run_id or str(uuid.uuid4()),
        payload=payload,
    )
    _ring.append(event)
    for q in list(_subscribers):
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            logger.warning(
                "SSE subscriber queue full — dropping event %s (subscribers=%d)",
                event_type, len(_subscribers),
            )
    logger.debug("control.event %s run_id=%s", event_type, event.run_id)


def get_recent_events() -> list[ControlEvent]:
    return list(_ring)


def subscribe() -> asyncio.Queue[ControlEvent]:
    q: asyncio.Queue[ControlEvent] = asyncio.Queue(maxsize=200)
    _subscribers.add(q)
    return q


def unsubscribe(q: asyncio.Queue[ControlEvent]) -> None:
    _subscribers.discard(q)
