"""
Admin-provisioned web chat enrollment invites — see
docs/household-identity-and-access-design.md Option A's security contract.

A URL is a transferable bearer credential, proof of possession only — not
proof of a person. This token is a short-lived, one-time bootstrap secret
spent establishing a passkey (`mark_invite_used`, called by
app.webchat.api_webauthn only *after* WebAuthn verification has already
succeeded, immediately before credential creation), never a standing
credential itself. A failed or malformed ceremony attempt no longer burns
the invite at all — fixed 2026-08-31 (security re-review finding BR-06;
the invite used to be marked used *before* verification, so a bad first
attempt permanently locked out a legitimate holder's retry).

`WebChatInvite` lives in cache.db, `WebAuthnCredential` in users.db —
different SQLite files, so true cross-database atomicity isn't available
anywhere in this codebase (no other feature has it either). `mark_invite_used`
is a single-database compare-and-swap: it only succeeds if the invite was
still unclaimed at that instant, so two concurrent claim attempts (both
past verification) can't both succeed, even though a failure *after* that
point (credential creation itself failing) still burns the invite. That's
an accepted, now much narrower tradeoff — the legitimate user asks the
admin for a new invite — favoring "never double-redeemable" over "always
resumable after a failure."
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
from app.models.cache import WebChatInvite

logger = logging.getLogger(__name__)


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@dataclass
class InviteInfo:
    token: str
    user_id: str
    household_id: str
    expires_at: datetime


def create_invite(user_id: str, household_id: str, created_by_user_id: str) -> InviteInfo:
    """Issue a new invite, revoking any existing unclaimed one for this
    user first — only one outstanding attempt at a time."""
    settings = get_settings()
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(minutes=settings.webauthn_invite_ttl_minutes)
    raw_token = secrets.token_urlsafe(32)

    with cache_session() as session:
        existing = session.exec(
            select(WebChatInvite).where(
                WebChatInvite.user_id == user_id,
                WebChatInvite.used_at.is_(None),  # type: ignore[union-attr]
                WebChatInvite.revoked_at.is_(None),  # type: ignore[union-attr]
            )
        ).all()
        for row in existing:
            row.revoked_at = now
            session.add(row)

        session.add(
            WebChatInvite(
                token_hash=_hash_token(raw_token),
                user_id=user_id,
                household_id=household_id,
                created_by_user_id=created_by_user_id,
                expires_at=expires_at,
            )
        )
        session.commit()

    record_audit_event(
        "webchat.invite_created",
        household_id,
        actor_user_id=created_by_user_id,
        target_user_id=user_id,
    )
    logger.info("Web chat invite created for user_id=%s by=%s", user_id, created_by_user_id)
    return InviteInfo(
        token=raw_token, user_id=user_id, household_id=household_id, expires_at=expires_at
    )


def get_valid_invite(token: str) -> InviteInfo | None:
    """Look up an invite without consuming it — used to show 'you're
    claiming an account for <name>' before the passkey ceremony starts."""
    if not token:
        return None
    token_hash = _hash_token(token)
    now = datetime.now(timezone.utc)
    with cache_session() as session:
        row = session.exec(
            select(WebChatInvite).where(WebChatInvite.token_hash == token_hash)
        ).first()
        if row is None or row.used_at is not None or row.revoked_at is not None:
            return None
        expires_at = row.expires_at.replace(tzinfo=timezone.utc)
        if expires_at < now:
            return None
        return InviteInfo(
            token=token, user_id=row.user_id, household_id=row.household_id, expires_at=expires_at
        )


def mark_invite_used(token: str) -> bool:
    """Atomically transition an invite from unclaimed to used. Returns
    False if it was already used/revoked/expired/unknown — callers must
    not proceed with credential creation in that case."""
    token_hash = _hash_token(token)
    now = datetime.now(timezone.utc)
    household_id = ""
    user_id = ""
    with cache_session() as session:
        row = session.exec(
            select(WebChatInvite).where(WebChatInvite.token_hash == token_hash)
        ).first()
        if row is None or row.used_at is not None or row.revoked_at is not None:
            return False
        if row.expires_at.replace(tzinfo=timezone.utc) < now:
            return False
        row.used_at = now
        session.add(row)
        session.commit()
        household_id = row.household_id
        user_id = row.user_id

    record_audit_event("webchat.invite_used", household_id, target_user_id=user_id)
    return True


def revoke_invite_for_user(user_id: str, household_id: str, revoked_by_user_id: str) -> None:
    now = datetime.now(timezone.utc)
    with cache_session() as session:
        rows = session.exec(
            select(WebChatInvite).where(
                WebChatInvite.user_id == user_id,
                WebChatInvite.used_at.is_(None),  # type: ignore[union-attr]
                WebChatInvite.revoked_at.is_(None),  # type: ignore[union-attr]
            )
        ).all()
        for row in rows:
            row.revoked_at = now
            session.add(row)
        session.commit()
    record_audit_event(
        "webchat.invite_revoked",
        household_id,
        actor_user_id=revoked_by_user_id,
        target_user_id=user_id,
    )
