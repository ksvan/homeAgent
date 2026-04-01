"""Unit tests for app.control.events — ring buffer and SSE subscriber logic."""
from __future__ import annotations

from app.control.events import (
    _ring,
    _subscribers,
    emit,
    get_recent_events,
    subscribe,
    unsubscribe,
)


def _clear() -> None:
    """Reset module-level state between tests."""
    _ring.clear()
    _subscribers.clear()


def test_emit_adds_to_ring() -> None:
    _clear()
    emit("run.start", {"foo": "bar"}, run_id="r1")
    events = get_recent_events()
    assert len(events) == 1
    assert events[0].event_type == "run.start"
    assert events[0].run_id == "r1"
    assert events[0].payload == {"foo": "bar"}


def test_ring_buffer_capped() -> None:
    _clear()
    for i in range(200):
        emit("test.event", {"i": i}, run_id=f"r{i}")
    events = get_recent_events()
    assert len(events) == 150  # _MAX_RING


def test_subscriber_receives_events() -> None:
    _clear()
    q = subscribe()
    emit("run.complete", {"ok": True}, run_id="r1")
    assert not q.empty()
    event = q.get_nowait()
    assert event.event_type == "run.complete"
    unsubscribe(q)


def test_unsubscribe_removes_queue() -> None:
    _clear()
    q = subscribe()
    assert q in _subscribers
    unsubscribe(q)
    assert q not in _subscribers


def test_full_queue_does_not_raise() -> None:
    """When a subscriber queue is full, emit should not raise."""
    _clear()
    q = subscribe()
    # Fill the queue (maxsize=200)
    for i in range(200):
        emit("fill", {"i": i}, run_id=f"f{i}")
    # This emit should log a warning but not raise
    emit("overflow", {"extra": True}, run_id="overflow")
    assert q.full()
    unsubscribe(q)


def test_emit_generates_run_id_when_empty() -> None:
    _clear()
    emit("test.event", {})
    events = get_recent_events()
    assert len(events) == 1
    assert events[0].run_id  # should be a non-empty UUID string
