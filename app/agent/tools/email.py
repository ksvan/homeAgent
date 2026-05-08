"""Agent tools for the email channel."""
from __future__ import annotations

import logging

from pydantic_ai import Agent, RunContext

from app.agent.agent import AgentDeps

logger = logging.getLogger(__name__)


def register_email_tools(agent: Agent[AgentDeps, str]) -> None:

    @agent.tool
    async def check_email_now(ctx: RunContext[AgentDeps]) -> str:
        """
        Pull recent messages from the AgentMail inbox and process any that
        have not been seen yet. Use when the user says they forwarded an email
        and the webhook may have missed it, or to retry failed intake.

        Returns a summary of what was found and what was queued for confirmation.
        """
        from app.config import get_settings
        from app.email.agentmail_client import list_messages
        from app.email.models import EmailMessage
        from app.email.repository import get_by_provider_ids, save
        from app.email.service import process_email_message

        settings = get_settings()
        if not settings.feature_email_channel:
            return "Email channel is not enabled."

        limit = settings.email_channel_force_check_limit
        msgs = list_messages(settings.agentmail_inbox_id, limit=limit, label="received")

        if not msgs:
            return "No recent emails found in the inbox."

        queued = 0
        already_seen = 0
        for m in msgs:
            existing = get_by_provider_ids("agentmail", m.message_id)
            if existing:
                already_seen += 1
                continue

            from email.utils import parseaddr
            _, from_email = parseaddr(m.from_display)
            from_email = from_email.strip().lower()

            row = EmailMessage(
                provider="agentmail",
                provider_message_id=m.message_id,
                provider_thread_id=m.thread_id,
                provider_inbox_id=m.inbox_id,
                channel_user_id=from_email,
                from_email=from_email,
                subject=m.subject,
                status="RECEIVED",
            )
            saved = save(row)
            import asyncio
            asyncio.create_task(process_email_message(saved))
            queued += 1

        parts = []
        if queued:
            parts.append(f"{queued} new email(s) queued for processing")
        if already_seen:
            parts.append(f"{already_seen} already seen")
        return "; ".join(parts) + "." if parts else "Nothing new to process."
