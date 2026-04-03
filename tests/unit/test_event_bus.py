"""Unit tests for app.control.event_bus and app.control.dispatcher helpers."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from app.control.dispatcher import (
    _build_prompt_envelope,
    _in_quiet_hours,
    _is_on_cooldown,
    _matches_value_filter,
    _rule_last_triggered,
)
from app.control.event_bus import InboundEvent, enqueue_event, get_event

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _event(**kwargs) -> InboundEvent:
    defaults = dict(
        source="homey",
        event_type="device_state_change",
        household_id="hh-1",
        entity_id="dev-1",
        payload={"capability": "alarm_motion", "value": True, "entity_name": "Hallway PIR"},
        timestamp=datetime.now(timezone.utc),
        raw={},
    )
    defaults.update(kwargs)
    return InboundEvent(**defaults)


def _rule(**kwargs) -> MagicMock:
    r = MagicMock()
    r.id = "rule-1"
    r.name = "Motion alert"
    r.source = "homey"
    r.event_type = "device_state_change"
    r.entity_id = "*"
    r.capability = None
    r.value_filter_json = None
    r.condition_json = None
    r.cooldown_minutes = 5
    r.prompt_template = "Motion in {zone} at {time}."
    r.user_id = "user-1"
    r.channel_user_id = "123"
    for k, v in kwargs.items():
        setattr(r, k, v)
    return r


# ---------------------------------------------------------------------------
# Event bus
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_enqueue_and_dequeue() -> None:
    ev = _event()
    enqueue_event(ev)
    result = await asyncio.wait_for(get_event(), timeout=1.0)
    assert result is ev


def test_enqueue_drop_on_full(monkeypatch) -> None:
    """Enqueueing beyond maxsize should log a warning and not raise."""
    import app.control.event_bus as bus_module

    # Replace bus with a tiny queue
    tiny_q: asyncio.Queue = asyncio.Queue(maxsize=2)
    monkeypatch.setattr(bus_module, "_event_bus", tiny_q)

    enqueue_event(_event())
    enqueue_event(_event())
    # Third enqueue should not raise
    enqueue_event(_event())
    assert bus_module.bus_size() == 2


# ---------------------------------------------------------------------------
# Cooldown
# ---------------------------------------------------------------------------


def test_not_on_cooldown_initially() -> None:
    _rule_last_triggered.clear()
    assert not _is_on_cooldown(_rule())


def test_on_cooldown_after_trigger() -> None:
    from datetime import timedelta

    _rule_last_triggered.clear()
    r = _rule(cooldown_minutes=10)
    # Simulate a recent trigger
    _rule_last_triggered[r.id] = datetime.now(timezone.utc) - timedelta(minutes=3)
    assert _is_on_cooldown(r)


def test_cooldown_expired() -> None:
    from datetime import timedelta

    _rule_last_triggered.clear()
    r = _rule(cooldown_minutes=5)
    _rule_last_triggered[r.id] = datetime.now(timezone.utc) - timedelta(minutes=10)
    assert not _is_on_cooldown(r)


# ---------------------------------------------------------------------------
# Quiet hours
# ---------------------------------------------------------------------------


def test_no_quiet_hours_configured() -> None:
    assert not _in_quiet_hours(_rule(condition_json=None))


def test_quiet_hours_same_day(monkeypatch) -> None:
    """09:00–12:00 quiet window; simulate current time inside it."""
    import app.control.dispatcher as disp

    monkeypatch.setattr(
        disp,
        "_in_quiet_hours",
        lambda rule: _check_quiet(rule, "10:30"),
    )
    # Direct function test without monkeypatching internal clock
    cond = json.dumps({"quiet_hours_start": "09:00", "quiet_hours_end": "12:00"})
    r = _rule(condition_json=cond)

    # Patch datetime.now inside the module to return 10:30
    from unittest.mock import patch

    fixed_dt = datetime(2026, 4, 3, 10, 30, tzinfo=timezone.utc)
    with patch("app.control.dispatcher.datetime") as mock_dt:
        mock_dt.now.return_value = fixed_dt
        assert _in_quiet_hours(r)


def test_quiet_hours_outside_window() -> None:
    from unittest.mock import patch

    cond = json.dumps({"quiet_hours_start": "22:00", "quiet_hours_end": "07:00"})
    r = _rule(condition_json=cond)

    fixed_dt = datetime(2026, 4, 3, 14, 0, tzinfo=timezone.utc)
    with patch("app.control.dispatcher.datetime") as mock_dt:
        mock_dt.now.return_value = fixed_dt
        assert not _in_quiet_hours(r)


def test_quiet_hours_overnight_inside() -> None:
    from unittest.mock import patch

    cond = json.dumps({"quiet_hours_start": "22:00", "quiet_hours_end": "07:00"})
    r = _rule(condition_json=cond)

    fixed_dt = datetime(2026, 4, 3, 23, 30, tzinfo=timezone.utc)
    with patch("app.control.dispatcher.datetime") as mock_dt:
        mock_dt.now.return_value = fixed_dt
        assert _in_quiet_hours(r)


def _check_quiet(rule, time_str: str) -> bool:
    """Helper — not used directly; kept for clarity."""
    return False


# ---------------------------------------------------------------------------
# Value filter
# ---------------------------------------------------------------------------


def test_value_filter_eq_match() -> None:
    ev = _event(payload={"value": True})
    r = _rule(value_filter_json=json.dumps({"eq": True}))
    assert _matches_value_filter(ev, r)


def test_value_filter_eq_no_match() -> None:
    ev = _event(payload={"value": False})
    r = _rule(value_filter_json=json.dumps({"eq": True}))
    assert not _matches_value_filter(ev, r)


def test_value_filter_gt() -> None:
    ev = _event(payload={"value": 25.0})
    r = _rule(value_filter_json=json.dumps({"gt": 20.0}))
    assert _matches_value_filter(ev, r)


def test_value_filter_gt_fail() -> None:
    ev = _event(payload={"value": 15.0})
    r = _rule(value_filter_json=json.dumps({"gt": 20.0}))
    assert not _matches_value_filter(ev, r)


def test_value_filter_none_passes_all() -> None:
    ev = _event(payload={"value": "anything"})
    r = _rule(value_filter_json=None)
    assert _matches_value_filter(ev, r)


def test_value_filter_invalid_json_passes() -> None:
    ev = _event(payload={"value": True})
    r = _rule(value_filter_json="not-json")
    assert _matches_value_filter(ev, r)


# ---------------------------------------------------------------------------
# Prompt envelope
# ---------------------------------------------------------------------------


def test_build_prompt_envelope_contains_rule_name() -> None:
    ev = _event()
    r = _rule(name="Hallway motion", prompt_template="Motion at {time} in {zone}.")
    result = _build_prompt_envelope(ev, r)
    assert "Hallway motion" in result
    assert "## Event Trigger" in result
    assert "## Task" in result
    assert "Motion at" in result


def test_build_prompt_envelope_interpolates_fields() -> None:
    ev = _event(
        payload={
            "capability": "alarm_motion",
            "value": True,
            "zone": "Kitchen",
            "entity_name": "Kitchen Sensor",
        }
    )
    r = _rule(prompt_template="Motion in {zone} ({entity_name}).")
    result = _build_prompt_envelope(ev, r)
    assert "Kitchen" in result
    assert "Kitchen Sensor" in result
