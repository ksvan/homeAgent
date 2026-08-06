"""
Web chat session issuance and validation.

Opaque bearer token in cache.db, sliding expiry — see
docs/web-chat-channel-design.md "Identity & login". No password: a session
is created the moment a household member picks themselves from the login
screen (`app.webchat.api`), which is the entire v1 trust model (LAN-only).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlmodel import select

from app.config import get_settings
from app.db import cache_session
from app.models.cache import WebChatSession

logger = logging.getLogger(__name__)


@dataclass
class SessionInfo:
    token: str
    user_id: str
    household_id: str
    expires_at: datetime


def create_session(user_id: str, household_id: str) -> SessionInfo:
    """Issue a new session for a picked user. Returns the bearer token."""
    settings = get_settings()
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(days=settings.web_chat_session_ttl_days)

    with cache_session() as session:
        row = WebChatSession(
            user_id=user_id,
            household_id=household_id,
            expires_at=expires_at,
        )
        session.add(row)
        session.commit()
        session.refresh(row)
        logger.info("Web chat session created (user_id=%s)", user_id)
        return SessionInfo(
            token=row.token,
            user_id=row.user_id,
            household_id=row.household_id,
            expires_at=expires_at,
        )


def get_session(token: str) -> SessionInfo | None:
    """Validate a bearer token. Returns None if missing or expired (and
    deletes expired rows). Does NOT slide expiry — call touch_session for that."""
    if not token:
        return None
    with cache_session() as session:
        row = session.exec(select(WebChatSession).where(WebChatSession.token == token)).first()
        if row is None:
            return None
        now = datetime.now(timezone.utc)
        expires_at = row.expires_at.replace(tzinfo=timezone.utc)
        if expires_at < now:
            session.delete(row)
            session.commit()
            return None
        return SessionInfo(
            token=row.token,
            user_id=row.user_id,
            household_id=row.household_id,
            expires_at=expires_at,
        )


def touch_session(token: str) -> None:
    """Slide the session's expiry forward from now — call on each authenticated use."""
    settings = get_settings()
    now = datetime.now(timezone.utc)
    with cache_session() as session:
        row = session.exec(select(WebChatSession).where(WebChatSession.token == token)).first()
        if row is None:
            return
        row.last_seen_at = now
        row.expires_at = now + timedelta(days=settings.web_chat_session_ttl_days)
        session.add(row)
        session.commit()


def delete_session(token: str) -> None:
    """Revoke a session (logout / switch user)."""
    with cache_session() as session:
        row = session.exec(select(WebChatSession).where(WebChatSession.token == token)).first()
        if row:
            session.delete(row)
            session.commit()
