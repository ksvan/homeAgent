"""
Email intake processing pipeline.

Takes a persisted EmailMessage (status=RECEIVED) through:
  sender mapping → full message fetch → preprocess → Telegram confirmation
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlmodel import select

from app.db import users_session
from app.email.agentmail_client import fetch_message
from app.email.confirmation import create_confirmation
from app.email.models import EmailMessage
from app.email.preprocessor import build_intake_summary, build_telegram_prompt
from app.email.repository import update_status
from app.models.users import ChannelMapping

logger = logging.getLogger(__name__)

_MAX_ATTEMPTS = 3
_RETRY_BASE_SECONDS = 60
_BURST_WINDOW_MINUTES = 10
_BURST_THRESHOLD = 3


def _emit(event_type: str, payload: dict) -> None:  # type: ignore[type-arg]
    try:
        from app.control.admin_events import emit_admin_event
        emit_admin_event(event_type, payload)
    except Exception:
        pass


def _schedule_retry(row_id: str, attempt_count: int, reason: str) -> None:
    delay = _RETRY_BASE_SECONDS * (2 ** attempt_count)
    next_attempt = datetime.now(timezone.utc) + timedelta(seconds=delay)
    update_status(
        row_id,
        "FAILED_RETRYABLE",
        status_reason=reason,
        next_attempt_at=next_attempt,
        last_error=reason,
    )
    _emit("email.retry_scheduled", {"email_message_id": row_id, "attempt": attempt_count})


def _dead_letter(row_id: str, reason: str) -> None:
    update_status(row_id, "DEAD_LETTER", status_reason=reason, last_error=reason)
    _emit("email.dead_lettered", {"email_message_id": row_id, "reason": reason})
    logger.warning("Email intake: dead-lettered message %s — %s", row_id, reason)


def _resolve_user(from_email: str) -> ChannelMapping | None:
    with users_session() as session:
        row = session.exec(
            select(ChannelMapping).where(
                ChannelMapping.channel == "email",
                ChannelMapping.channel_user_id == from_email,
            )
        ).first()
        if row is None:
            return None
        return ChannelMapping(
            id=row.id,
            user_id=row.user_id,
            channel=row.channel,
            channel_user_id=row.channel_user_id,
        )


def _get_telegram_channel_user_id(user_id: str) -> str | None:
    with users_session() as session:
        row = session.exec(
            select(ChannelMapping).where(
                ChannelMapping.user_id == user_id,
                ChannelMapping.channel == "telegram",
            )
        ).first()
        return row.channel_user_id if row else None


def _get_household_id(user_id: str) -> str | None:
    from app.models.users import User

    with users_session() as session:
        user = session.exec(select(User).where(User.id == user_id)).first()
        return user.household_id if user else None


def _pending_confirmation_count(user_id: str) -> int:
    """Count NEEDS_CONFIRMATION rows for this user — used for burst digest."""
    from app.db import cache_session

    with cache_session() as session:
        rows = session.exec(
            select(EmailMessage).where(
                EmailMessage.user_id == user_id,
                EmailMessage.status == "NEEDS_CONFIRMATION",
            )
        ).all()
        return len(rows)


async def process_email_message(row: EmailMessage) -> None:
    """
    Full processing pipeline for one EmailMessage row.
    Called from the background task spawned by the webhook handler or retry worker.
    """
    from app.config import get_settings

    settings = get_settings()

    # Guard against too many attempts
    attempt = row.attempt_count
    if attempt >= _MAX_ATTEMPTS:
        _dead_letter(row.id, f"max_attempts_exceeded:{attempt}")
        return

    # Increment attempt count
    update_status(row.id, "CLASSIFYING", attempt_count=attempt + 1)

    # --- Sender mapping ---
    mapping = _resolve_user(row.channel_user_id)
    if mapping is None:
        update_status(row.id, "IGNORED", status_reason="unmapped_sender")
        _emit("email.sender_unmapped", {
            "email_message_id": row.id,
            "from_email": row.channel_user_id,
        })
        return

    user_id = mapping.user_id
    household_id = _get_household_id(user_id)
    if not household_id:
        update_status(row.id, "IGNORED", status_reason="user_no_household")
        return

    telegram_cuid = _get_telegram_channel_user_id(user_id)
    if not telegram_cuid:
        logger.warning(
            "Email intake: user %s has no Telegram mapping (message_id=%s)", user_id, row.id
        )
        update_status(row.id, "IGNORED", status_reason="no_telegram_channel")
        return

    update_status(row.id, "CLASSIFYING", user_id=user_id, household_id=household_id)

    # --- Fetch full message from AgentMail API ---
    try:
        full_msg = fetch_message(settings.agentmail_inbox_id, row.provider_message_id)
    except Exception as exc:
        logger.warning("Email intake: fetch failed for %s: %s", row.provider_message_id, exc)
        _emit("email.full_fetch_failed", {"email_message_id": row.id, "error": str(exc)})
        _schedule_retry(row.id, attempt, f"fetch_failed: {exc}")
        return

    _emit("email.preprocessed", {"email_message_id": row.id})

    # --- Preprocess ---
    instruction, intake_summary = build_intake_summary(
        full_msg, max_chars=settings.email_channel_max_agent_chars
    )

    # --- Burst digest: if user already has several pending confirmations, send a digest ---
    pending = _pending_confirmation_count(user_id)
    from app.channels.registry import get_channel

    channel = get_channel()
    if channel is None:
        logger.warning("Email intake: no active channel (message_id=%s)", row.id)
        _schedule_retry(row.id, attempt, "no_channel")
        return

    if pending >= _BURST_THRESHOLD:
        digest = (
            f"📧 You have {pending + 1} unreviewed emails waiting.\n"
            f"Latest: {full_msg.subject[:60]!r} from {full_msg.from_email}\n\n"
            f"Reply with 'check email' to review them."
        )
        try:
            await channel.send_message(telegram_cuid, digest)
            _emit("email.confirmation_digest_requested", {
                "email_message_id": row.id,
                "user_id": user_id,
                "pending_count": pending,
            })
        except Exception as exc:
            logger.warning("Email intake: digest send failed: %s", exc)
        # Still create the confirmation so it's accessible via check_email_now
        token = create_confirmation(
            email_message_id=row.id,
            user_id=user_id,
            household_id=household_id,
            intake_text=intake_summary,
        )
        update_status(
            row.id,
            "NEEDS_CONFIRMATION",
            confirmation_id=token,
            instruction_text=instruction,
            intake_summary_text=intake_summary,
            updated_at=datetime.now(timezone.utc),
        )
        return

    # --- Normal confirmation prompt ---
    telegram_prompt = build_telegram_prompt(full_msg, instruction)
    token = create_confirmation(
        email_message_id=row.id,
        user_id=user_id,
        household_id=household_id,
        intake_text=intake_summary,
    )
    update_status(
        row.id,
        "NEEDS_CONFIRMATION",
        confirmation_id=token,
        instruction_text=instruction,
        intake_summary_text=intake_summary,
        updated_at=datetime.now(timezone.utc),
    )

    try:
        await channel.send_email_intake_prompt(telegram_cuid, telegram_prompt, token)
        _emit("email.confirmation_requested", {
            "email_message_id": row.id,
            "user_id": user_id,
        })
        logger.info(
            "Email intake: confirmation sent to user=%s (message_id=%s token=%s)",
            user_id, row.id, token,
        )
    except Exception as exc:
        logger.warning("Email intake: confirmation send failed: %s", exc)
        _schedule_retry(row.id, attempt, f"confirmation_send_failed: {exc}")
