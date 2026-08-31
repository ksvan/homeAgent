"""
Admin-provisioned Telegram account-linking codes — see
docs/household-identity-and-access-design.md Option B.

Mirrors app.webchat.invites' security contract (hashed, short-lived,
single-use, revocable, durably audited) for a different bootstrap secret:
a short, human-typable code entered via the `/link <code>` Telegram
command, rather than a URL clicked in a browser. Consuming a code never
touches `User.name` matching logic — it's a direct `User.id` lookup, by
design (see Option B: "never inferred by name-matching").
"""

from __future__ import annotations

import hashlib
import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlmodel import select

from app.config import get_settings
from app.control.audit import record_audit_event
from app.db import cache_session
from app.models.cache import TelegramLinkCode

logger = logging.getLogger(__name__)

# Excludes visually ambiguous characters (0/O, 1/I/L) — this code is read
# and typed by a person, unlike the invite URL token.
_ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"
_CODE_LENGTH = 12


def _generate_code() -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(_CODE_LENGTH))


def _hash_code(code: str) -> str:
    return hashlib.sha256(code.strip().upper().encode()).hexdigest()


@dataclass
class LinkCodeInfo:
    code: str
    user_id: str
    household_id: str
    expires_at: datetime


def create_link_code(user_id: str, household_id: str, created_by_user_id: str) -> LinkCodeInfo:
    """Issue a new linking code, revoking any existing unclaimed one for
    this user first — only one outstanding attempt at a time."""
    settings = get_settings()
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(minutes=settings.webauthn_invite_ttl_minutes)
    raw_code = _generate_code()

    with cache_session() as session:
        existing = session.exec(
            select(TelegramLinkCode).where(
                TelegramLinkCode.user_id == user_id,
                TelegramLinkCode.used_at.is_(None),  # type: ignore[union-attr]
                TelegramLinkCode.revoked_at.is_(None),  # type: ignore[union-attr]
            )
        ).all()
        for row in existing:
            row.revoked_at = now
            session.add(row)

        session.add(
            TelegramLinkCode(
                code_hash=_hash_code(raw_code),
                user_id=user_id,
                household_id=household_id,
                created_by_user_id=created_by_user_id,
                expires_at=expires_at,
            )
        )
        session.commit()

    record_audit_event(
        "telegram.link_code_created",
        household_id,
        actor_user_id=created_by_user_id,
        target_user_id=user_id,
    )
    logger.info("Telegram link code created for user_id=%s by=%s", user_id, created_by_user_id)
    return LinkCodeInfo(
        code=raw_code, user_id=user_id, household_id=household_id, expires_at=expires_at
    )


def consume_link_code(code: str) -> LinkCodeInfo | None:
    """Atomically transition a code from unclaimed to used. Returns the
    code's info on success, or None if it was unknown/expired/used/revoked
    — callers must not proceed with linking in that case."""
    if not code:
        return None
    code_hash = _hash_code(code)
    now = datetime.now(timezone.utc)
    with cache_session() as session:
        row = session.exec(
            select(TelegramLinkCode).where(TelegramLinkCode.code_hash == code_hash)
        ).first()
        if row is None or row.used_at is not None or row.revoked_at is not None:
            return None
        expires_at = row.expires_at.replace(tzinfo=timezone.utc)
        if expires_at < now:
            return None
        row.used_at = now
        session.add(row)
        session.commit()
        return LinkCodeInfo(
            code=code, user_id=row.user_id, household_id=row.household_id, expires_at=expires_at
        )


def revoke_link_code_for_user(user_id: str, household_id: str, revoked_by_user_id: str) -> None:
    now = datetime.now(timezone.utc)
    with cache_session() as session:
        rows = session.exec(
            select(TelegramLinkCode).where(
                TelegramLinkCode.user_id == user_id,
                TelegramLinkCode.used_at.is_(None),  # type: ignore[union-attr]
                TelegramLinkCode.revoked_at.is_(None),  # type: ignore[union-attr]
            )
        ).all()
        for row in rows:
            row.revoked_at = now
            session.add(row)
        session.commit()
    record_audit_event(
        "telegram.link_code_revoked",
        household_id,
        actor_user_id=revoked_by_user_id,
        target_user_id=user_id,
    )
