"""Household-scoped repository for IntegrationAccount rows.

Generic across providers (see docs/oda-grocery-mcp-tool-design.md), but the
only provider wired up today is Oda, so `get_valid_access_token` dispatches
directly to `app.oda.oauth` rather than through a provider registry that
doesn't have a second member yet.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlmodel import select

from app.db import users_session
from app.integrations.crypto import DecryptionError, decrypt, encrypt
from app.models.integrations import IntegrationAccount

logger = logging.getLogger(__name__)

# Refresh a bit before actual expiry to avoid racing a request that's already
# in flight when the token expires.
_EXPIRY_SAFETY_MARGIN = timedelta(seconds=60)

# One lock per (household_id, provider) — single process, in-memory is enough
# (see docs/oda-grocery-mcp-tool-design.md "Concurrency-safe refresh").
_refresh_locks: dict[tuple[str, str], asyncio.Lock] = {}


def _lock_for(household_id: str, provider: str) -> asyncio.Lock:
    key = (household_id, provider)
    lock = _refresh_locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _refresh_locks[key] = lock
    return lock


def get_account(household_id: str, provider: str) -> IntegrationAccount | None:
    """Return the (still-encrypted) account row, or None if not connected."""
    with users_session() as session:
        account = session.exec(
            select(IntegrationAccount).where(
                IntegrationAccount.household_id == household_id,
                IntegrationAccount.provider == provider,
            )
        ).first()
        if account is None:
            return None
        session.expunge(account)
        return account


def upsert_account(
    *,
    household_id: str,
    provider: str,
    connected_by_user_id: str,
    client_id: str,
    client_secret: str,
    access_token: str,
    refresh_token: str,
    expires_at: datetime,
) -> IntegrationAccount:
    """Create or replace the connected account for this household + provider.

    Reconnecting while already connected is a normal re-auth, not an error —
    the existing row is overwritten.
    """
    now = datetime.now(timezone.utc)
    with users_session() as session:
        existing = session.exec(
            select(IntegrationAccount).where(
                IntegrationAccount.household_id == household_id,
                IntegrationAccount.provider == provider,
            )
        ).first()

        if existing is None:
            existing = IntegrationAccount(
                household_id=household_id,
                provider=provider,
                connected_by_user_id=connected_by_user_id,
                access_token="",
                refresh_token="",
                expires_at=expires_at,
            )

        existing.connected_by_user_id = connected_by_user_id
        existing.client_id = client_id
        existing.client_secret = encrypt(client_secret)
        existing.access_token = encrypt(access_token)
        existing.refresh_token = encrypt(refresh_token)
        existing.expires_at = expires_at
        existing.updated_at = now

        session.add(existing)
        session.commit()
        session.refresh(existing)
        session.expunge(existing)
        return existing


def delete_account(household_id: str, provider: str) -> None:
    with users_session() as session:
        account = session.exec(
            select(IntegrationAccount).where(
                IntegrationAccount.household_id == household_id,
                IntegrationAccount.provider == provider,
            )
        ).first()
        if account is not None:
            session.delete(account)
            session.commit()


async def get_valid_access_token(household_id: str, provider: str) -> str | None:
    """Return a currently-valid access token for this household + provider,
    refreshing it first if it's expired or about to expire. Returns None if
    not connected, or if the stored tokens can no longer be decrypted
    (e.g. app_secret_key rotated) — treated as "not connected", not a crash.
    """
    account = get_account(household_id, provider)
    if account is None:
        return None

    # See naive-UTC note below — DB round-trip strips tzinfo.
    now = datetime.utcnow()
    if account.expires_at > now + _EXPIRY_SAFETY_MARGIN:
        try:
            return decrypt(account.access_token)
        except DecryptionError:
            logger.warning(
                "Could not decrypt Oda access token for household=%s — dropping account",
                household_id,
            )
            delete_account(household_id, provider)
            return None

    lock = _lock_for(household_id, provider)
    async with lock:
        # Re-check after acquiring the lock — another concurrent caller may
        # have already refreshed while we were waiting.
        account = get_account(household_id, provider)
        if account is None:
            return None
        # Compare with a naive UTC "now" — SQLite round-trips DateTime columns
        # as naive (see app.policy.pending for the same convention), so
        # account.expires_at read back from the DB has no tzinfo even though
        # it was written from an aware datetime.
        now = datetime.utcnow()
        if account.expires_at > now + _EXPIRY_SAFETY_MARGIN:
            try:
                return decrypt(account.access_token)
            except DecryptionError:
                logger.warning(
                    "Could not decrypt Oda access token for household=%s — dropping account",
                    household_id,
                )
                delete_account(household_id, provider)
                return None

        try:
            refresh_token_plain = decrypt(account.refresh_token)
            client_secret_plain = decrypt(account.client_secret) if account.client_secret else ""
        except DecryptionError:
            logger.warning(
                "Could not decrypt Oda refresh token for household=%s — dropping account",
                household_id,
            )
            delete_account(household_id, provider)
            return None

        from app.oda import oauth as oda_oauth

        try:
            metadata = await oda_oauth.discover_metadata()
            token = await oda_oauth.refresh_access_token(
                metadata,
                client_id=account.client_id,
                client_secret=client_secret_plain,
                refresh_token=refresh_token_plain,
            )
        except oda_oauth.OdaOAuthError:
            logger.warning("Oda token refresh failed for household=%s", household_id, exc_info=True)
            return None

        upsert_account(
            household_id=household_id,
            provider=provider,
            connected_by_user_id=account.connected_by_user_id,
            client_id=account.client_id,
            client_secret=client_secret_plain,
            access_token=token.access_token,
            refresh_token=token.refresh_token or refresh_token_plain,
            expires_at=token.expires_at,
        )
        return token.access_token
