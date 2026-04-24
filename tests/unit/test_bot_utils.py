"""Unit tests for app.bot._is_rate_limited().

The rate limiter is a pure sliding-window function using monotonic time.
Tests control time by monkeypatching `monotonic` in the bot module
(imported there as `from time import monotonic`).
"""
from __future__ import annotations

import pytest

import app.bot as bot_module
from app.bot import _is_rate_limited


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def clear_rate_cache() -> None:
    """Reset the in-memory rate-limit cache before each test."""
    bot_module._user_call_times.clear()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_first_call_is_not_rate_limited(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bot_module, "monotonic", lambda: 0.0)
    assert _is_rate_limited(1001, limit_per_minute=5) is False


def test_calls_within_limit_are_not_rate_limited(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bot_module, "monotonic", lambda: 0.0)
    limit = 3
    for _ in range(limit):
        assert _is_rate_limited(1001, limit_per_minute=limit) is False


def test_exceeding_limit_triggers_rate_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bot_module, "monotonic", lambda: 0.0)
    limit = 3
    for _ in range(limit):
        _is_rate_limited(1001, limit_per_minute=limit)
    # (limit + 1)th call in the same second window — blocked
    assert _is_rate_limited(1001, limit_per_minute=limit) is True


def test_different_users_have_independent_counters(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bot_module, "monotonic", lambda: 0.0)
    limit = 2
    for _ in range(limit):
        _is_rate_limited(1001, limit_per_minute=limit)
    assert _is_rate_limited(1001, limit_per_minute=limit) is True
    # User 1002 is unaffected by user 1001's calls
    assert _is_rate_limited(1002, limit_per_minute=limit) is False


def test_old_timestamps_slide_out_of_window(monkeypatch: pytest.MonkeyPatch) -> None:
    # Fill to limit at t=0
    monkeypatch.setattr(bot_module, "monotonic", lambda: 0.0)
    limit = 2
    for _ in range(limit):
        _is_rate_limited(1001, limit_per_minute=limit)
    assert _is_rate_limited(1001, limit_per_minute=limit) is True

    # Advance 61 s — all previous calls are outside the 60 s window
    monkeypatch.setattr(bot_module, "monotonic", lambda: 61.0)
    assert _is_rate_limited(1001, limit_per_minute=limit) is False


def test_window_boundary_exact(monkeypatch: pytest.MonkeyPatch) -> None:
    """Call at exactly t=60 s after t=0 is outside the window (filter is < 60.0)."""
    monkeypatch.setattr(bot_module, "monotonic", lambda: 0.0)
    _is_rate_limited(1001, limit_per_minute=1)

    # At t=60.0: now - 0 = 60.0, which is NOT < 60.0 → the call slides out
    monkeypatch.setattr(bot_module, "monotonic", lambda: 60.0)
    assert _is_rate_limited(1001, limit_per_minute=1) is False


def test_limit_of_one_allows_first_blocks_second(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bot_module, "monotonic", lambda: 0.0)
    assert _is_rate_limited(1001, limit_per_minute=1) is False
    assert _is_rate_limited(1001, limit_per_minute=1) is True
