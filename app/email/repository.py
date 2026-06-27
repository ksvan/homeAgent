"""EmailMessage persistence and deduplication."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlmodel import select

from app.db import cache_session
from app.email.models import EmailMessage

logger = logging.getLogger(__name__)


def get_by_provider_ids(
    provider: str,
    provider_message_id: str,
) -> EmailMessage | None:
    with cache_session() as session:
        row = session.exec(
            select(EmailMessage).where(
                EmailMessage.provider == provider,
                EmailMessage.provider_message_id == provider_message_id,
            )
        ).first()
        if row is None:
            return None
        session.expunge(row)
        return row


def get_by_delivery_id(delivery_id: str) -> EmailMessage | None:
    with cache_session() as session:
        row = session.exec(
            select(EmailMessage).where(EmailMessage.provider_delivery_id == delivery_id)
        ).first()
        if row is None:
            return None
        session.expunge(row)
        return row


def save(msg: EmailMessage) -> EmailMessage:
    with cache_session() as session:
        session.add(msg)
        session.commit()
        session.refresh(msg)
        session.expunge(msg)
        return msg


def update_status(
    message_id: str,
    status: str,
    status_reason: str | None = None,
    **extra: object,
) -> None:
    with cache_session() as session:
        row = session.exec(select(EmailMessage).where(EmailMessage.id == message_id)).first()
        if row is None:
            logger.warning("EmailMessage %s not found for status update", message_id)
            return
        row.status = status
        row.updated_at = datetime.now(timezone.utc)
        if status_reason is not None:
            row.status_reason = status_reason
        for k, v in extra.items():
            setattr(row, k, v)
        session.add(row)
        session.commit()
