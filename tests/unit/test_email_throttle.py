"""Tests for app/email/throttle.py — check_and_record() and pending_count_for_user().

Module-level state is reset between tests via a fixture that clears the
internal dicts and list directly. No mocking of monotonic() needed for
most cases — the state is cleared instead.
"""

from __future__ import annotations

import pytest

import app.email.throttle as _throttle
from app.email.throttle import check_and_record, pending_count_for_user


@pytest.fixture(autouse=True)
def reset_throttle_state() -> None:
    """Clear all in-memory throttle state before each test."""
    _throttle._sender_times.clear()
    _throttle._user_times.clear()
    _throttle._global_times.clear()


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_first_email_is_allowed() -> None:
    result = check_and_record("alice@example.com", "user1")
    assert result is None


def test_first_email_without_user_id_is_allowed() -> None:
    result = check_and_record("unknown@example.com", None)
    assert result is None


def test_multiple_emails_within_limit_are_allowed() -> None:
    for _ in range(5):
        result = check_and_record("alice@example.com", "user1")
        assert result is None


# ---------------------------------------------------------------------------
# Sender rate limit
# ---------------------------------------------------------------------------


def test_sender_blocked_after_limit() -> None:
    limit = _throttle._SENDER_LIMIT_PER_HOUR
    # Fill up to the limit
    for _ in range(limit):
        check_and_record("spammer@example.com", None)
    # Next one should be blocked
    result = check_and_record("spammer@example.com", None)
    assert result is not None
    assert "sender_rate_limit" in result
    assert "spammer@example.com" in result


def test_sender_limit_is_per_sender() -> None:
    limit = _throttle._SENDER_LIMIT_PER_HOUR
    for _ in range(limit):
        check_and_record("spammer@example.com", None)
    # Different sender is unaffected
    result = check_and_record("legit@example.com", None)
    assert result is None


# ---------------------------------------------------------------------------
# User rate limit
# ---------------------------------------------------------------------------


def test_user_blocked_after_limit() -> None:
    from time import monotonic

    limit = _throttle._USER_LIMIT_PER_HOUR
    # Pre-seed user bucket directly so we don't exhaust the global bucket
    _throttle._user_times["user1"] = [monotonic() for _ in range(limit)]
    result = check_and_record("fresh@example.com", "user1")
    assert result is not None
    assert "user_rate_limit" in result
    assert "user1" in result


def test_user_limit_not_applied_when_user_id_is_none() -> None:
    # Even many emails without user_id should not trigger user_rate_limit
    for i in range(_throttle._USER_LIMIT_PER_HOUR + 5):
        check_and_record(f"anon{i}@example.com", None)
    result = check_and_record("anon999@example.com", None)
    # May hit global limit, but must not be user_rate_limit
    assert result is None or "user_rate_limit" not in result


# ---------------------------------------------------------------------------
# Global rate limit
# ---------------------------------------------------------------------------


def test_global_limit_blocks_all_senders() -> None:
    limit = _throttle._GLOBAL_LIMIT_PER_MINUTE
    for i in range(limit):
        check_and_record(f"sender{i}@example.com", f"user{i}")
    result = check_and_record("new@example.com", "newuser")
    assert result == "global_rate_limit"


def test_global_limit_checked_before_sender_limit() -> None:
    # Fill the global bucket
    global_limit = _throttle._GLOBAL_LIMIT_PER_MINUTE
    for i in range(global_limit):
        check_and_record(f"unique{i}@example.com", None)
    # Now a brand-new sender still hits global limit first
    result = check_and_record("fresh@example.com", None)
    assert result == "global_rate_limit"


# ---------------------------------------------------------------------------
# pending_count_for_user
# ---------------------------------------------------------------------------


def test_pending_count_zero_for_unknown_user() -> None:
    assert pending_count_for_user("nobody") == 0


def test_pending_count_reflects_recent_intakes() -> None:
    check_and_record("a@example.com", "user1")
    check_and_record("b@example.com", "user1")
    assert pending_count_for_user("user1") == 2


def test_pending_count_is_user_scoped() -> None:
    check_and_record("a@example.com", "user1")
    check_and_record("b@example.com", "user2")
    assert pending_count_for_user("user1") == 1
    assert pending_count_for_user("user2") == 1


def test_pending_count_respects_window() -> None:
    # Default 10-minute window — current entries are always within it
    check_and_record("a@example.com", "user1")
    assert pending_count_for_user("user1", window_minutes=10) == 1


def test_pending_count_zero_outside_window() -> None:
    # Manually inject a timestamp well outside the window
    import app.email.throttle as t
    from time import monotonic

    t._user_times["user1"] = [monotonic() - 3600]  # 1 hour ago
    assert pending_count_for_user("user1", window_minutes=10) == 0


# ---------------------------------------------------------------------------
# State recording
# ---------------------------------------------------------------------------


def test_record_increments_sender_count() -> None:
    check_and_record("alice@example.com", None)
    check_and_record("alice@example.com", None)
    assert len(_throttle._sender_times["alice@example.com"]) == 2


def test_record_increments_user_count() -> None:
    check_and_record("a@example.com", "user1")
    check_and_record("b@example.com", "user1")
    assert len(_throttle._user_times["user1"]) == 2


def test_record_increments_global_count() -> None:
    check_and_record("a@example.com", "user1")
    check_and_record("b@example.com", "user2")
    assert len(_throttle._global_times) == 2


def test_blocked_email_does_not_increment_counts() -> None:
    limit = _throttle._GLOBAL_LIMIT_PER_MINUTE
    for i in range(limit):
        check_and_record(f"s{i}@example.com", None)
    before = len(_throttle._global_times)
    check_and_record("blocked@example.com", None)
    assert len(_throttle._global_times) == before
