"""
Optional email acknowledgement reply after Telegram confirmation.

Gated on EMAIL_CHANNEL_ALLOW_REPLY_TO=true (default false).
Replies only to the mapped outer From sender. Never reply-all.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def send_ack_reply(
    inbox_id: str,
    provider_message_id: str,
    from_email: str,
) -> None:
    """
    Send a short acknowledgement reply in the same AgentMail thread.
    Called after the user confirms the intake in Telegram.
    Silently skipped if the feature flag is off.
    """
    from app.config import get_settings
    from app.email.agentmail_client import get_client

    settings = get_settings()
    if not getattr(settings, "email_channel_allow_reply_to", False):
        return

    try:
        client = get_client()
        client.inboxes.messages.reply(
            inbox_id=inbox_id,
            message_id=provider_message_id,
            to=[from_email],
            text=(
                "I received your email and have sent a confirmation request "
                "via Telegram. I will follow up there."
            ),
        )
        logger.info("Email ack reply sent to %s (message_id=%s)", from_email, provider_message_id)
    except Exception as exc:
        logger.warning("Email ack reply failed for %s: %s", provider_message_id, exc)
