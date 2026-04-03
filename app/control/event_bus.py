"""
Inbound event bus.

Provides:
  InboundEvent  — normalised schema for all events entering HomeAgent
  enqueue_event — non-blocking enqueue (drops on overflow)
  get_event     — async dequeue (used by dispatcher)

Design notes:
- In-process asyncio.Queue only — no persistence, no replay.
- Events are state signals, not commands. Dropping on overflow is acceptable.
- household_id is resolved server-side (single-household system) by the
  webhook receiver before enqueueing; it is not expected in external payloads.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_BUS_MAX_SIZE = 256
_event_bus: asyncio.Queue[InboundEvent] = asyncio.Queue(maxsize=_BUS_MAX_SIZE)


@dataclass
class InboundEvent:
    source: str        # "homey" | "internal" | future: "calendar"
    event_type: str    # "device_state_change" | "flow_trigger" | "threshold"
    household_id: str
    entity_id: str     # device UUID, zone ID, etc.
    payload: dict = field(default_factory=dict)   # event-specific data
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    raw: dict = field(default_factory=dict)        # original payload for audit


def enqueue_event(event: InboundEvent) -> None:
    """Non-blocking enqueue. Logs and drops if the bus is full."""
    try:
        _event_bus.put_nowait(event)
    except asyncio.QueueFull:
        logger.warning(
            "Event bus full (%d slots) — dropping event source=%s type=%s entity=%s",
            _BUS_MAX_SIZE,
            event.source,
            event.event_type,
            event.entity_id,
        )


async def get_event() -> InboundEvent:
    """Block until an event is available, then return it."""
    return await _event_bus.get()


def bus_size() -> int:
    """Return the current number of queued events (for monitoring/tests)."""
    return _event_bus.qsize()
