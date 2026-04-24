"""Unit tests for app.scheduler.scheduled_prompts._build_trigger().

_build_trigger is pure logic: no DB, no I/O.  It maps a (recurrence, time_of_day)
pair to an APScheduler trigger object, or raises ValueError for invalid input.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger

from app.scheduler.scheduled_prompts import _build_trigger


# ---------------------------------------------------------------------------
# once
# ---------------------------------------------------------------------------


def test_once_with_run_at_returns_date_trigger() -> None:
    run_at = datetime(2026, 6, 1, 9, 0, tzinfo=timezone.utc)
    trigger = _build_trigger("once", "09:00", run_at=run_at)
    assert isinstance(trigger, DateTrigger)


def test_once_without_run_at_raises() -> None:
    with pytest.raises(ValueError, match="run_at"):
        _build_trigger("once", "09:00", run_at=None)


# ---------------------------------------------------------------------------
# daily
# ---------------------------------------------------------------------------


def test_daily_returns_cron_trigger() -> None:
    trigger = _build_trigger("daily", "07:30")
    assert isinstance(trigger, CronTrigger)


@pytest.mark.parametrize(
    "time_of_day,expected_hour,expected_minute",
    [
        ("00:00", 0, 0),
        ("07:30", 7, 30),
        ("12:00", 12, 0),
        ("23:59", 23, 59),
    ],
)
def test_daily_parses_time(time_of_day: str, expected_hour: int, expected_minute: int) -> None:
    trigger = _build_trigger("daily", time_of_day)
    assert isinstance(trigger, CronTrigger)
    assert trigger.hour == expected_hour
    assert trigger.minute == expected_minute


# ---------------------------------------------------------------------------
# weekly
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("day", ["mon", "tue", "wed", "thu", "fri", "sat", "sun"])
def test_weekly_all_valid_days(day: str) -> None:
    trigger = _build_trigger(f"weekly:{day}", "08:00")
    assert isinstance(trigger, CronTrigger)


def test_weekly_invalid_day_raises() -> None:
    with pytest.raises(ValueError, match="Unknown day"):
        _build_trigger("weekly:xyz", "08:00")


def test_weekly_preserves_time() -> None:
    trigger = _build_trigger("weekly:fri", "20:15")
    assert isinstance(trigger, CronTrigger)
    assert trigger.hour == 20
    assert trigger.minute == 15


# ---------------------------------------------------------------------------
# monthly
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("day_num", [1, 15, 28])
def test_monthly_valid_days(day_num: int) -> None:
    trigger = _build_trigger(f"monthly:{day_num}", "10:00")
    assert isinstance(trigger, CronTrigger)


def test_monthly_day_0_raises() -> None:
    with pytest.raises(ValueError, match="1.28"):
        _build_trigger("monthly:0", "10:00")


def test_monthly_day_29_raises() -> None:
    with pytest.raises(ValueError, match="1.28"):
        _build_trigger("monthly:29", "10:00")


def test_monthly_non_numeric_raises() -> None:
    with pytest.raises(ValueError, match="monthly"):
        _build_trigger("monthly:last", "10:00")


# ---------------------------------------------------------------------------
# Invalid time_of_day format
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad_time", ["730", "7:3:0", "noon", "", "25:00"])
def test_invalid_time_of_day_raises(bad_time: str) -> None:
    with pytest.raises(ValueError):
        _build_trigger("daily", bad_time)


# ---------------------------------------------------------------------------
# Unknown recurrence
# ---------------------------------------------------------------------------


def test_unknown_recurrence_raises() -> None:
    with pytest.raises(ValueError, match="Unknown recurrence"):
        _build_trigger("hourly", "08:00")
