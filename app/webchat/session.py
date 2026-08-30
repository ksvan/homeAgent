"""
Web chat session issuance and validation.

Opaque bearer token in cache.db — see
docs/household-identity-and-access-design.md Option D / Goal 7. Only a
SHA-256 hash of the token is ever persisted; the raw token exists in memory
at issuance and in the caller's hands afterward, never on disk, so a stolen
DB backup can't be replayed as a live session.

Sliding expiry (`expires_at`, extended by `touch_session`) is capped by an
absolute expiry (`absolute_expires_at`, fixed at creation) — a session
can't outlive that cap no matter how often it's used. `revoke_session`
marks an explicit logout/admin-revoke without deleting the row, so it
stays visible for audit; a session that merely expired is deleted outright
since natural expiry isn't an audit-worthy event the way an explicit
revoke is.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlmodel import select

from app.config import get_settings
from app.db import cache_session
from app.models.cache import WebChatSession

logger = logging.getLogger(__name__)


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@dataclass
class SessionInfo:
    token: str
    user_id: str
    household_id: str
    expires_at: datetime


def create_session(user_id: str, household_id: str) -> SessionInfo:
    """Issue a new session for a picked user. Returns the bearer token —
    the only time it's ever available in plaintext; only its hash is
    stored."""
    settings = get_settings()
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(days=settings.web_chat_session_ttl_days)
    absolute_expires_at = now + timedelta(days=settings.web_chat_session_absolute_ttl_days)
    raw_token = secrets.token_urlsafe(32)

    with cache_session() as session:
        row = WebChatSession(
            token_hash=_hash_token(raw_token),
            user_id=user_id,
            household_id=household_id,
            expires_at=expires_at,
            absolute_expires_at=absolute_expires_at,
        )
        session.add(row)
        session.commit()
        logger.info("Web chat session created (user_id=%s)", user_id)
        return SessionInfo(
            token=raw_token,
            user_id=user_id,
            household_id=household_id,
            expires_at=expires_at,
        )


def get_session(token: str) -> SessionInfo | None:
    """Validate a bearer token. Returns None if missing, revoked, or
    expired (deleting rows that expired naturally — see module docstring
    for why a revoked row is kept instead). Does NOT slide expiry — call
    touch_session for that."""
    if not token:
        return None
    token_hash = _hash_token(token)
    with cache_session() as session:
        row = session.exec(
            select(WebChatSession).where(WebChatSession.token_hash == token_hash)
        ).first()
        if row is None:
            return None
        if row.revoked_at is not None:
            return None
        now = datetime.now(timezone.utc)
        expires_at = row.expires_at.replace(tzinfo=timezone.utc)
        absolute_expires_at = row.absolute_expires_at.replace(tzinfo=timezone.utc)
        if expires_at < now or absolute_expires_at < now:
            session.delete(row)
            session.commit()
            return None
        return SessionInfo(
            token=token,
            user_id=row.user_id,
            household_id=row.household_id,
            expires_at=expires_at,
        )


def touch_session(token: str) -> None:
    """Slide the session's expiry forward from now, capped at its absolute
    expiry — call on each authenticated use."""
    settings = get_settings()
    now = datetime.now(timezone.utc)
    token_hash = _hash_token(token)
    with cache_session() as session:
        row = session.exec(
            select(WebChatSession).where(WebChatSession.token_hash == token_hash)
        ).first()
        if row is None or row.revoked_at is not None:
            return
        absolute_expires_at = row.absolute_expires_at.replace(tzinfo=timezone.utc)
        slid = now + timedelta(days=settings.web_chat_session_ttl_days)
        row.last_seen_at = now
        row.expires_at = min(slid, absolute_expires_at)
        session.add(row)
        session.commit()


def revoke_session(token: str) -> None:
    """Explicitly end a session (logout / switch user / admin revoke).
    Marks revoked_at rather than deleting, so it stays visible for audit —
    see module docstring."""
    token_hash = _hash_token(token)
    with cache_session() as session:
        row = session.exec(
            select(WebChatSession).where(WebChatSession.token_hash == token_hash)
        ).first()
        if row and row.revoked_at is None:
            row.revoked_at = datetime.now(timezone.utc)
            session.add(row)
            session.commit()
