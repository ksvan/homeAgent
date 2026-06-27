"""
In-memory intake throttle for the email channel.

Three independent layers:
  1. Per-sender  — max emails from one From address per hour
  2. Per-user    — max emails for one mapped user per hour
  3. Global      — max emails accepted per minute across all senders

All state is in-memory and resets on restart (intentional — same pattern as
the Telegram rate limiter in bot.py). A restart clears any transient burst.
"""

from __future__ import annotations

from collections import defaultdict
from time import monotonic

# Hardcoded limits (promote to config if operational experience warrants it)
_SENDER_LIMIT_PER_HOUR = 20
_USER_LIMIT_PER_HOUR = 30
_GLOBAL_LIMIT_PER_MINUTE = 30

_sender_times: dict[str, list[float]] = defaultdict(list)
_user_times: dict[str, list[float]] = defaultdict(list)
_global_times: list[float] = []


def _prune(times: list[float], window: float) -> list[float]:
    now = monotonic()
    return [t for t in times if now - t < window]


def check_and_record(sender_email: str, user_id: str | None) -> str | None:
    """
    Check all three throttle layers and record the intake if allowed.

    Returns None if allowed, or a reason string if throttled.
    Caller should mark the row RATE_LIMITED with the returned reason.
    """
    global _global_times

    now = monotonic()

    # Global per-minute
    _global_times = _prune(_global_times, 60.0)
    if len(_global_times) >= _GLOBAL_LIMIT_PER_MINUTE:
        return "global_rate_limit"

    # Per-sender per-hour
    _sender_times[sender_email] = _prune(_sender_times[sender_email], 3600.0)
    if len(_sender_times[sender_email]) >= _SENDER_LIMIT_PER_HOUR:
        return f"sender_rate_limit:{sender_email}"

    # Per-user per-hour (only if sender is mapped)
    if user_id:
        _user_times[user_id] = _prune(_user_times[user_id], 3600.0)
        if len(_user_times[user_id]) >= _USER_LIMIT_PER_HOUR:
            return f"user_rate_limit:{user_id}"

    # All clear — record
    _global_times.append(now)
    _sender_times[sender_email].append(now)
    if user_id:
        _user_times[user_id].append(now)

    return None


def pending_count_for_user(user_id: str, window_minutes: int = 10) -> int:
    """Count recent intake events for a user within the burst window."""
    times = _user_times.get(user_id, [])
    cutoff = monotonic() - window_minutes * 60.0
    return sum(1 for t in times if t >= cutoff)
