"""EmailIntakeConfirmation persistence."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlmodel import select

from app.db import cache_session
from app.email.models import EmailIntakeConfirmation

_TTL_SECONDS = 300  # 5-minute window for email intake confirmations


def create_confirmation(
    email_message_id: str,
    user_id: str,
    household_id: str,
    intake_text: str,
) -> str:
    """Persist a new EmailIntakeConfirmation and return its token."""
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=_TTL_SECONDS)
    confirmation = EmailIntakeConfirmation(
        email_message_id=email_message_id,
        user_id=user_id,
        household_id=household_id,
        intake_text=intake_text,
        expires_at=expires_at,
    )
    with cache_session() as session:
        session.add(confirmation)
        session.commit()
        session.refresh(confirmation)
        return confirmation.token


def get_confirmation(token: str) -> EmailIntakeConfirmation | None:
    with cache_session() as session:
        row = session.exec(
            select(EmailIntakeConfirmation).where(EmailIntakeConfirmation.token == token)
        ).first()
        if row is None:
            return None
        if row.expires_at.replace(tzinfo=timezone.utc) < datetime.now(timezone.utc):
            session.delete(row)
            session.commit()
            return None
        return EmailIntakeConfirmation(
            token=row.token,
            email_message_id=row.email_message_id,
            user_id=row.user_id,
            household_id=row.household_id,
            intake_text=row.intake_text,
            expires_at=row.expires_at,
            created_at=row.created_at,
        )


def delete_confirmation(token: str) -> None:
    with cache_session() as session:
        row = session.exec(
            select(EmailIntakeConfirmation).where(EmailIntakeConfirmation.token == token)
        ).first()
        if row:
            session.delete(row)
            session.commit()
