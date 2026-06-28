"""Tests for app/flights/diff.py — compute_changes() and should_notify().

All models are plain dataclasses so no DB or mocking is needed.
"""

from datetime import date, datetime, timezone

import pytest

from app.flights.diff import compute_changes, should_notify
from app.flights.models import (
    DEFAULT_NOTIFY_POLICY,
    FlightStatusChange,
    FlightStatusSnapshot,
    FlightWatch,
)

# ---------------------------------------------------------------------------
# Factories
# ---------------------------------------------------------------------------

_NOW = datetime(2025, 6, 1, 12, 0, tzinfo=timezone.utc)
_DATE = date(2025, 6, 1)


def _watch(**kwargs) -> FlightWatch:
    defaults = dict(
        id="w1",
        household_id="hh1",
        user_id="u1",
        channel_user_id="tg:123",
        carrier_code="SK",
        flight_number="451",
        scheduled_departure_date=_DATE,
        provider="aerodatabox",
        status="ACTIVE",
    )
    return FlightWatch(**{**defaults, **kwargs})


def _snap(**kwargs) -> FlightStatusSnapshot:
    defaults = dict(
        id="s1",
        watch_id="w1",
        provider="aerodatabox",
        fetched_at=_NOW,
    )
    return FlightStatusSnapshot(**{**defaults, **kwargs})


_POLICY = DEFAULT_NOTIFY_POLICY.copy()


def _change_types(changes: list[FlightStatusChange]) -> list[str]:
    return [c.change_type for c in changes]


# ---------------------------------------------------------------------------
# First snapshot (previous=None)
# ---------------------------------------------------------------------------


class TestFirstSnapshot:
    def test_no_changes_on_normal_first_snapshot(self):
        watch = _watch()
        current = _snap()
        assert compute_changes(watch, None, current, _POLICY) == []

    def test_cancelled_on_first_snapshot_notified(self):
        watch = _watch()
        current = _snap(cancelled=True)
        changes = compute_changes(watch, None, current, _POLICY)
        assert len(changes) == 1
        assert changes[0].change_type == "cancelled"
        assert changes[0].severity == "critical"

    def test_delayed_on_first_snapshot_not_notified(self):
        watch = _watch()
        current = _snap(delay_minutes=60)
        assert compute_changes(watch, None, current, _POLICY) == []


# ---------------------------------------------------------------------------
# Cancellation
# ---------------------------------------------------------------------------


class TestCancellation:
    def test_flight_cancelled(self):
        watch = _watch()
        prev = _snap(cancelled=False)
        curr = _snap(cancelled=True)
        changes = compute_changes(watch, prev, curr, _POLICY)
        assert _change_types(changes) == ["cancelled"]
        assert changes[0].severity == "critical"

    def test_already_cancelled_no_repeat(self):
        watch = _watch()
        prev = _snap(cancelled=True)
        curr = _snap(cancelled=True)
        assert compute_changes(watch, prev, curr, _POLICY) == []

    def test_cancellation_suppressed_by_policy(self):
        watch = _watch()
        prev = _snap(cancelled=False)
        curr = _snap(cancelled=True)
        policy = {**_POLICY, "notify_cancellations": False}
        assert compute_changes(watch, prev, curr, policy) == []


# ---------------------------------------------------------------------------
# Diversion
# ---------------------------------------------------------------------------


class TestDiversion:
    def test_flight_diverted(self):
        watch = _watch()
        prev = _snap(diverted=False)
        curr = _snap(diverted=True, diversion_airport="OSL")
        changes = compute_changes(watch, prev, curr, _POLICY)
        assert _change_types(changes) == ["diverted"]
        assert changes[0].severity == "critical"
        assert "OSL" in changes[0].summary

    def test_diversion_summary_without_airport(self):
        watch = _watch()
        prev = _snap(diverted=False)
        curr = _snap(diverted=True, diversion_airport=None)
        changes = compute_changes(watch, prev, curr, _POLICY)
        assert changes[0].change_type == "diverted"
        assert "diverted" in changes[0].summary

    def test_already_diverted_no_repeat(self):
        watch = _watch()
        prev = _snap(diverted=True)
        curr = _snap(diverted=True)
        assert compute_changes(watch, prev, curr, _POLICY) == []

    def test_diversion_suppressed_by_policy(self):
        watch = _watch()
        prev = _snap(diverted=False)
        curr = _snap(diverted=True)
        policy = {**_POLICY, "notify_diversions": False}
        assert compute_changes(watch, prev, curr, policy) == []


# ---------------------------------------------------------------------------
# Delay
# ---------------------------------------------------------------------------


class TestDelay:
    @pytest.mark.parametrize(
        "prev_min, curr_min, expect_change",
        [
            (0, 15, True),   # crosses threshold, delta == threshold
            (0, 30, True),   # well over threshold
            (0, 14, False),  # below threshold
            (15, 20, False), # delta < threshold (only 5 min change)
            (15, 30, True),  # both over threshold, delta == threshold
            (30, 45, True),  # both over threshold, delta == threshold
            (30, 44, False), # delta only 14 min, below threshold
        ],
    )
    def test_delay_change_threshold(self, prev_min, curr_min, expect_change):
        watch = _watch()
        prev = _snap(delay_minutes=prev_min)
        curr = _snap(delay_minutes=curr_min)
        changes = compute_changes(watch, prev, curr, _POLICY)
        delay_changes = [c for c in changes if c.change_type == "delay_changed"]
        assert bool(delay_changes) == expect_change

    def test_delay_warning_severity_under_60(self):
        watch = _watch()
        prev = _snap(delay_minutes=0)
        curr = _snap(delay_minutes=30)
        changes = compute_changes(watch, prev, curr, _POLICY)
        assert changes[0].severity == "warning"

    def test_delay_critical_severity_at_60(self):
        watch = _watch()
        prev = _snap(delay_minutes=0)
        curr = _snap(delay_minutes=60)
        changes = compute_changes(watch, prev, curr, _POLICY)
        assert changes[0].severity == "critical"

    def test_delay_resolved(self):
        watch = _watch()
        prev = _snap(delay_minutes=30)
        curr = _snap(delay_minutes=5)
        changes = compute_changes(watch, prev, curr, _POLICY)
        assert _change_types(changes) == ["delay_resolved"]
        assert changes[0].severity == "info"

    def test_delay_resolved_to_zero(self):
        watch = _watch()
        prev = _snap(delay_minutes=15)
        curr = _snap(delay_minutes=0)
        changes = compute_changes(watch, prev, curr, _POLICY)
        assert _change_types(changes) == ["delay_resolved"]

    def test_no_delay_change_when_both_none(self):
        watch = _watch()
        prev = _snap(delay_minutes=None)
        curr = _snap(delay_minutes=None)
        assert compute_changes(watch, prev, curr, _POLICY) == []

    def test_custom_delay_threshold(self):
        watch = _watch()
        prev = _snap(delay_minutes=0)
        curr = _snap(delay_minutes=10)
        policy = {**_POLICY, "delay_threshold_minutes": 10}
        changes = compute_changes(watch, prev, curr, policy)
        assert _change_types(changes) == ["delay_changed"]


# ---------------------------------------------------------------------------
# Gate changes
# ---------------------------------------------------------------------------


class TestGateChanges:
    def test_gate_changed(self):
        watch = _watch()
        prev = _snap(departure_gate="A10")
        curr = _snap(departure_gate="B5")
        changes = compute_changes(watch, prev, curr, _POLICY)
        assert _change_types(changes) == ["gate_changed"]
        assert changes[0].severity == "warning"
        assert "A10" in changes[0].summary
        assert "B5" in changes[0].summary

    def test_gate_assigned_for_first_time(self):
        watch = _watch()
        prev = _snap(departure_gate=None)
        curr = _snap(departure_gate="C3")
        changes = compute_changes(watch, prev, curr, _POLICY)
        assert _change_types(changes) == ["gate_assigned"]
        assert "C3" in changes[0].summary

    def test_no_change_same_gate(self):
        watch = _watch()
        prev = _snap(departure_gate="A10")
        curr = _snap(departure_gate="A10")
        gate_changes = [c for c in compute_changes(watch, prev, curr, _POLICY) if "gate" in c.change_type]
        assert gate_changes == []

    def test_gate_removed_no_event(self):
        watch = _watch()
        prev = _snap(departure_gate="A10")
        curr = _snap(departure_gate=None)
        gate_changes = [c for c in compute_changes(watch, prev, curr, _POLICY) if "gate" in c.change_type]
        assert gate_changes == []

    def test_gate_changes_suppressed_by_policy(self):
        watch = _watch()
        prev = _snap(departure_gate="A10")
        curr = _snap(departure_gate="B5")
        policy = {**_POLICY, "notify_gate_changes": False}
        assert compute_changes(watch, prev, curr, policy) == []


# ---------------------------------------------------------------------------
# Terminal changes
# ---------------------------------------------------------------------------


class TestTerminalChanges:
    def test_terminal_changed(self):
        watch = _watch()
        prev = _snap(departure_terminal="1")
        curr = _snap(departure_terminal="2")
        changes = compute_changes(watch, prev, curr, _POLICY)
        assert _change_types(changes) == ["terminal_changed"]
        assert "1" in changes[0].summary
        assert "2" in changes[0].summary

    def test_no_change_same_terminal(self):
        watch = _watch()
        prev = _snap(departure_terminal="1")
        curr = _snap(departure_terminal="1")
        terminal_changes = [c for c in compute_changes(watch, prev, curr, _POLICY) if "terminal" in c.change_type]
        assert terminal_changes == []

    def test_terminal_changes_suppressed_by_policy(self):
        watch = _watch()
        prev = _snap(departure_terminal="1")
        curr = _snap(departure_terminal="2")
        policy = {**_POLICY, "notify_terminal_changes": False}
        assert compute_changes(watch, prev, curr, policy) == []


# ---------------------------------------------------------------------------
# Boarding
# ---------------------------------------------------------------------------


class TestBoarding:
    def test_boarding_started(self):
        watch = _watch()
        prev = _snap(state="SCHEDULED")
        curr = _snap(state="BOARDING")
        changes = compute_changes(watch, prev, curr, _POLICY)
        assert _change_types(changes) == ["boarding_started"]
        assert changes[0].severity == "warning"

    def test_already_boarding_no_repeat(self):
        watch = _watch()
        prev = _snap(state="BOARDING")
        curr = _snap(state="BOARDING")
        boarding_changes = [c for c in compute_changes(watch, prev, curr, _POLICY) if c.change_type == "boarding_started"]
        assert boarding_changes == []

    def test_boarding_suppressed_by_policy(self):
        watch = _watch()
        prev = _snap(state="SCHEDULED")
        curr = _snap(state="BOARDING")
        policy = {**_POLICY, "notify_boarding": False}
        assert compute_changes(watch, prev, curr, policy) == []


# ---------------------------------------------------------------------------
# Baggage
# ---------------------------------------------------------------------------


class TestBaggage:
    def test_baggage_assigned(self):
        watch = _watch()
        prev = _snap(baggage_claim=None)
        curr = _snap(baggage_claim="Belt 4")
        changes = compute_changes(watch, prev, curr, _POLICY)
        assert _change_types(changes) == ["baggage_assigned"]
        assert changes[0].severity == "info"
        assert "Belt 4" in changes[0].summary

    def test_baggage_unchanged_no_event(self):
        watch = _watch()
        prev = _snap(baggage_claim="Belt 4")
        curr = _snap(baggage_claim="Belt 4")
        baggage_changes = [c for c in compute_changes(watch, prev, curr, _POLICY) if "baggage" in c.change_type]
        assert baggage_changes == []

    def test_both_none_no_event(self):
        watch = _watch()
        prev = _snap(baggage_claim=None)
        curr = _snap(baggage_claim=None)
        assert compute_changes(watch, prev, curr, _POLICY) == []


# ---------------------------------------------------------------------------
# Multiple simultaneous changes
# ---------------------------------------------------------------------------


class TestMultipleChanges:
    def test_gate_and_delay_in_same_diff(self):
        watch = _watch()
        prev = _snap(departure_gate="A1", delay_minutes=0)
        curr = _snap(departure_gate="B2", delay_minutes=30)
        changes = compute_changes(watch, prev, curr, _POLICY)
        types = _change_types(changes)
        assert "gate_changed" in types
        assert "delay_changed" in types

    def test_cancelled_and_delayed_same_diff(self):
        watch = _watch()
        prev = _snap(cancelled=False, delay_minutes=0)
        curr = _snap(cancelled=True, delay_minutes=60)
        changes = compute_changes(watch, prev, curr, _POLICY)
        types = _change_types(changes)
        assert "cancelled" in types
        assert "delay_changed" in types


# ---------------------------------------------------------------------------
# flight_label on watch
# ---------------------------------------------------------------------------


class TestFlightLabel:
    def test_custom_label_used(self):
        watch = _watch(label="Dad's flight to Oslo")
        prev = _snap(cancelled=False)
        curr = _snap(cancelled=True)
        changes = compute_changes(watch, prev, curr, _POLICY)
        assert "Dad's flight to Oslo" in changes[0].summary
        assert changes[0].flight_label == "Dad's flight to Oslo"

    def test_default_label_from_carrier_and_number(self):
        watch = _watch(label=None)
        assert "SK451" in watch.flight_label


# ---------------------------------------------------------------------------
# should_notify
# ---------------------------------------------------------------------------


class TestShouldNotify:
    @pytest.mark.parametrize(
        "severity, quiet_hours_mode, expected",
        [
            ("critical", "urgent_only", True),
            ("warning", "urgent_only", True),
            ("info", "urgent_only", False),
            ("debug", "urgent_only", False),
            ("critical", "all", True),
            ("warning", "all", True),
            ("info", "all", True),
            ("debug", "all", True),
        ],
    )
    def test_should_notify(self, severity, quiet_hours_mode, expected):
        change = FlightStatusChange(
            watch_id="w1",
            flight_label="SK451",
            change_type="cancelled",
            severity=severity,
            summary="test",
        )
        policy = {**_POLICY, "quiet_hours_mode": quiet_hours_mode}
        assert should_notify(change, policy) == expected
