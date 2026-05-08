"""
Email intake processing pipeline.

Takes a persisted EmailMessage (status=RECEIVED) through:
  sender mapping → full message fetch → preprocess → Telegram confirmation
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlmodel import select

from app.db import users_session
from app.email.agentmail_client import fetch_message
from app.email.confirmation import create_confirmation
from app.email.models import EmailMessage
from app.email.preprocessor import build_intake_summary, build_telegram_prompt
from app.email.repository import update_status
from app.models.users import ChannelMapping

logger = logging.getLogger(__name__)


def _resolve_user(from_email: str) -> ChannelMapping | None:
    """Return the email ChannelMapping for the sender, or None if unmapped."""
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
    """Return the Telegram channel_user_id for a user, or None."""
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


async def process_email_message(row: EmailMessage) -> None:
    """
    Full processing pipeline for one EmailMessage row.
    Called from the background task spawned by the webhook handler.
    """
    from app.config import get_settings

    settings = get_settings()

    # --- Sender mapping ---
    mapping = _resolve_user(row.channel_user_id)
    if mapping is None:
        if settings.email_channel_require_mapped_sender:
            logger.info(
                "Email intake: unmapped sender %s — ignoring (message_id=%s)",
                row.channel_user_id,
                row.id,
            )
            update_status(row.id, "IGNORED", status_reason="unmapped_sender")
            return
        # If require_mapped_sender=false, still skip for now — no user to confirm with
        update_status(row.id, "IGNORED", status_reason="unmapped_sender_no_mapping")
        return

    user_id = mapping.user_id
    household_id = _get_household_id(user_id)
    if not household_id:
        update_status(row.id, "IGNORED", status_reason="user_no_household")
        return

    telegram_cuid = _get_telegram_channel_user_id(user_id)
    if not telegram_cuid:
        logger.warning(
            "Email intake: user %s has no Telegram mapping — cannot confirm (message_id=%s)",
            user_id,
            row.id,
        )
        update_status(row.id, "IGNORED", status_reason="no_telegram_channel")
        return

    update_status(row.id, "CLASSIFYING", user_id=user_id, household_id=household_id)

    # --- Fetch full message from AgentMail API ---
    try:
        full_msg = fetch_message(settings.agentmail_inbox_id, row.provider_message_id)
    except Exception as exc:
        logger.warning("Email intake: failed to fetch message %s: %s", row.provider_message_id, exc)
        update_status(row.id, "FAILED_RETRYABLE", status_reason=f"fetch_failed: {exc}")
        return

    # --- Preprocess ---
    instruction, intake_summary = build_intake_summary(
        full_msg, max_chars=settings.email_channel_max_agent_chars
    )
    telegram_prompt = build_telegram_prompt(full_msg, instruction)

    # --- Create confirmation ---
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

    # --- Send Telegram confirmation ---
    from app.channels.registry import get_channel

    channel = get_channel()
    if channel is None:
        logger.warning("Email intake: no active channel — cannot send confirmation")
        update_status(row.id, "FAILED_RETRYABLE", status_reason="no_channel")
        return

    try:
        await channel.send_email_intake_prompt(telegram_cuid, telegram_prompt, token)
        logger.info(
            "Email intake: confirmation sent to user=%s (message_id=%s token=%s)",
            user_id,
            row.id,
            token,
        )
    except Exception as exc:
        logger.warning("Email intake: failed to send Telegram confirmation: %s", exc)
        update_status(row.id, "FAILED_RETRYABLE", status_reason=f"confirmation_send_failed: {exc}")
