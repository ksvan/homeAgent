"""Delivery policy evaluation and run-history recording for proactive scheduled prompts."""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, time, timedelta, timezone
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Behaviour-kind defaults
# ---------------------------------------------------------------------------

_KIND_DEFAULTS: dict[str | None, dict] = {
    None: {"skip_if_empty": False, "skip_if_unchanged": False},
    "generic_prompt": {"skip_if_empty": False, "skip_if_unchanged": False},
    "morning_briefing": {"skip_if_empty": True, "skip_if_unchanged": False},
    "calendar_digest": {"skip_if_empty": True, "skip_if_unchanged": True},
    "energy_summary": {"skip_if_empty": True, "skip_if_unchanged": True},
    "watch_check": {
        "skip_if_empty": True,
        "skip_if_unchanged": True,
        "quiet_hours_start": "22:00",
        "quiet_hours_end": "07:00",
        "cooldown_minutes": 180,
    },
    "task_followup": {
        "skip_if_empty": False,
        "skip_if_unchanged": False,
        "quiet_hours_start": "22:00",
        "quiet_hours_end": "07:00",
    },
}

# Minimum output length (after strip) to be considered non-empty.
_MIN_OUTPUT_LEN = 10


def parse_delivery_policy(sp: object) -> dict:
    """Parse delivery_policy_json from a ScheduledPrompt, merged with kind defaults.

    Returns a dict with at least ``skip_if_empty`` and ``skip_if_unchanged`` keys.
    Malformed JSON is treated as empty (deliver always) with a logged warning.
    """
    kind = getattr(sp, "behavior_kind", None)
    defaults = _KIND_DEFAULTS.get(kind, _KIND_DEFAULTS[None]).copy()

    raw = getattr(sp, "delivery_policy_json", None)
    if raw:
        try:
            explicit = json.loads(raw)
            if isinstance(explicit, dict):
                defaults.update(explicit)
        except (json.JSONDecodeError, TypeError):
            logger.warning(
                "Malformed delivery_policy_json for prompt_id=%s — using defaults",
                getattr(sp, "id", "?"),
            )

    return defaults


def compute_output_hash(output: str) -> str:
    """Return a short SHA-256 hash of stripped output for change detection."""
    return hashlib.sha256(output.strip().encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Preflight — checked BEFORE the LLM call
# ---------------------------------------------------------------------------


def evaluate_preflight(
    sp: object,
    policy: dict,
    now_utc: datetime,
) -> tuple[bool, str | None]:
    """Decide whether this scheduled prompt should proceed to the LLM call.

    Returns ``(should_proceed, skip_reason)``.
    """
    # Quiet hours
    qh_start = policy.get("quiet_hours_start")
    qh_end = policy.get("quiet_hours_end")
    if qh_start and qh_end:
        if _in_quiet_hours(qh_start, qh_end, now_utc):
            return False, "quiet_hours"

    # Cooldown
    cooldown_min = policy.get("cooldown_minutes")
    if cooldown_min:
        last_fired = getattr(sp, "last_fired_at", None)
        if last_fired and (now_utc - last_fired) < timedelta(minutes=int(cooldown_min)):
            return False, "cooldown"

    # Daily delivery cap
    max_per_day = policy.get("max_deliveries_per_day")
    if max_per_day:
        prompt_id = getattr(sp, "id", None)
        if prompt_id and _delivered_today_count(prompt_id, now_utc) >= int(max_per_day):
            return False, "daily_cap"

    return True, None


def _in_quiet_hours(start_str: str, end_str: str, now_utc: datetime) -> bool:
    """Check if ``now_utc`` falls within quiet hours in the household timezone."""
    try:
        from zoneinfo import ZoneInfo

        from app.config import get_settings

        tz = ZoneInfo(get_settings().household_timezone)
        local_now = now_utc.astimezone(tz).time()
        start = time.fromisoformat(start_str)
        end = time.fromisoformat(end_str)

        if start <= end:
            # Same-day span (e.g. 01:00 – 06:00)
            return start <= local_now <= end
        else:
            # Overnight span (e.g. 22:00 – 07:00)
            return local_now >= start or local_now <= end
    except Exception:
        logger.warning("Quiet-hours check failed — proceeding", exc_info=True)
        return False


def _delivered_today_count(prompt_id: str, now_utc: datetime) -> int:
    """Count how many times this prompt was delivered today (UTC day)."""
    try:
        from sqlmodel import col, func, select

        from app.db import users_session
        from app.models.scheduled_prompts import ScheduledPromptRun

        start_of_day = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)
        with users_session() as session:
            count = session.exec(
                select(func.count()).where(
                    col(ScheduledPromptRun.prompt_id) == prompt_id,
                    col(ScheduledPromptRun.status) == "delivered",
                    col(ScheduledPromptRun.fired_at) >= start_of_day,
                )
            ).one()
            return int(count)
    except Exception:
        logger.warning("Daily cap count failed — proceeding", exc_info=True)
        return 0


# ---------------------------------------------------------------------------
# Postflight — checked AFTER the LLM call
# ---------------------------------------------------------------------------


def evaluate_postflight(
    output: str,
    sp: object,
    policy: dict,
) -> tuple[str, str | None]:
    """Decide whether the output should be delivered or suppressed.

    Returns ``(status, skip_reason)`` where status is ``"delivered"`` or ``"skipped"``.
    """
    # Skip if empty
    if policy.get("skip_if_empty") and len(output.strip()) < _MIN_OUTPUT_LEN:
        return "skipped", "empty_output"

    # Skip if unchanged
    if policy.get("skip_if_unchanged"):
        new_hash = compute_output_hash(output)
        last_hash = getattr(sp, "last_result_hash", None)
        if last_hash and new_hash == last_hash:
            return "skipped", "unchanged_output"

    return "delivered", None


# ---------------------------------------------------------------------------
# Run-history recording
# ---------------------------------------------------------------------------


def record_run(
    prompt_id: str,
    run_id: Optional[str],
    status: str,
    skip_reason: Optional[str],
    output: Optional[str],
    fired_at: datetime,
    finished_at: Optional[datetime] = None,
) -> None:
    """Write a ScheduledPromptRun row and update the prompt's last-run metadata.

    Failures are logged but never raised — delivery must not be blocked by audit writes.
    """
    try:
        from app.db import users_session
        from app.models.scheduled_prompts import ScheduledPrompt, ScheduledPromptRun

        output_hash = compute_output_hash(output) if output else None
        output_preview = output[:200] if output else None
        now = finished_at or datetime.now(timezone.utc)

        with users_session() as session:
            # Create run record
            run = ScheduledPromptRun(
                prompt_id=prompt_id,
                fired_at=fired_at,
                finished_at=now,
                status=status,
                skip_reason=skip_reason,
                run_id=run_id,
                output_hash=output_hash,
                output_preview=output_preview,
            )
            session.add(run)

            # Update prompt header
            sp = session.get(ScheduledPrompt, prompt_id)
            if sp:
                sp.last_fired_at = fired_at
                sp.last_status = status
                if output_hash:
                    sp.last_result_hash = output_hash
                if output_preview:
                    sp.last_result_preview = output_preview
                if status == "delivered":
                    sp.last_delivered_at = now
                session.add(sp)

            session.commit()
    except Exception:
        logger.warning(
            "Failed to record run for prompt_id=%s — continuing",
            prompt_id, exc_info=True,
        )
