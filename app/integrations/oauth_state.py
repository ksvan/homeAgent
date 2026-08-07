"""Household-scoped, one-time OAuth PKCE/state storage (cache.db).

See docs/oda-grocery-mcp-tool-design.md "New setting" / "Flow" — the
admin `connect` route writes a row here; the (not yet built) public
callback route consumes it exactly once.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlmodel import select

from app.db import cache_session
from app.integrations.crypto import decrypt, encrypt
from app.models.cache import OAuthState

# Human-timescale OAuth consent window — long enough to actually log into
# Oda and approve, short enough that a stale state row isn't usable later.
DEFAULT_TTL_SECONDS = 600


def save_state(
    *,
    state: str,
    provider: str,
    household_id: str,
    initiating_user_id: str,
    pkce_verifier: str,
    redirect_uri: str,
    client_id: str,
    client_secret: str,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
) -> None:
    now = datetime.utcnow()
    with cache_session() as session:
        session.add(
            OAuthState(
                state=state,
                provider=provider,
                household_id=household_id,
                initiating_user_id=initiating_user_id,
                pkce_verifier=pkce_verifier,
                redirect_uri=redirect_uri,
                client_id=client_id,
                client_secret=encrypt(client_secret),
                created_at=now,
                expires_at=now + timedelta(seconds=ttl_seconds),
            )
        )
        session.commit()


def consume_state(state: str) -> OAuthState | None:
    """Read + delete the row in one step — a state value is usable exactly
    once, whether or not it turns out to be expired. Returns None if the
    state is unknown or was expired.
    """
    with cache_session() as session:
        row = session.exec(select(OAuthState).where(OAuthState.state == state)).first()
        if row is None:
            return None

        # Snapshot fields before delete/commit — the ORM instance's
        # attributes aren't safely readable once its row is gone.
        snapshot = OAuthState(
            state=row.state,
            provider=row.provider,
            household_id=row.household_id,
            initiating_user_id=row.initiating_user_id,
            pkce_verifier=row.pkce_verifier,
            redirect_uri=row.redirect_uri,
            client_id=row.client_id,
            client_secret=row.client_secret,
            created_at=row.created_at,
            expires_at=row.expires_at,
        )
        session.delete(row)
        session.commit()

    if snapshot.expires_at < datetime.utcnow():
        return None
    snapshot.client_secret = decrypt(snapshot.client_secret)
    return snapshot
