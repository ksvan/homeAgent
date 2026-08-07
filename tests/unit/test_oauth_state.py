"""Unit tests for app.integrations.oauth_state — one-time PKCE/state storage
backing the Oda (and future providers') OAuth connect flow."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta

import pytest
from sqlmodel import Session

from app.integrations import oauth_state


@pytest.fixture(autouse=True)
def _secret_key(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("APP_SECRET_KEY", "unit-test-secret-key-not-a-real-fernet-key")
    yield
    get_settings.cache_clear()


@pytest.fixture
def db(monkeypatch: pytest.MonkeyPatch, in_memory_engine: object) -> Session:
    @contextmanager  # type: ignore[misc]
    def _session():
        with Session(in_memory_engine) as s:  # type: ignore[arg-type]
            yield s

    monkeypatch.setattr("app.integrations.oauth_state.cache_session", _session)
    with Session(in_memory_engine) as s:  # type: ignore[arg-type]
        yield s


def test_consume_returns_none_when_state_unknown(db: Session) -> None:
    assert oauth_state.consume_state("nonexistent") is None


def test_save_then_consume_round_trips(db: Session) -> None:
    oauth_state.save_state(
        state="state-1",
        provider="oda",
        household_id="hh-1",
        initiating_user_id="user-1",
        pkce_verifier="verifier-value",
        redirect_uri="https://home.example.com/integrations/oda/callback",
        client_id="client-abc",
        client_secret="client-secret-value",
    )

    row = oauth_state.consume_state("state-1")
    assert row is not None
    assert row.provider == "oda"
    assert row.household_id == "hh-1"
    assert row.initiating_user_id == "user-1"
    assert row.pkce_verifier == "verifier-value"
    assert row.redirect_uri == "https://home.example.com/integrations/oda/callback"
    assert row.client_id == "client-abc"
    assert row.client_secret == "client-secret-value"  # decrypted on the way out


def test_consume_is_one_time(db: Session) -> None:
    oauth_state.save_state(
        state="state-1",
        provider="oda",
        household_id="hh-1",
        initiating_user_id="user-1",
        pkce_verifier="verifier-value",
        redirect_uri="https://x/callback",
        client_id="client-abc",
        client_secret="",
    )

    first = oauth_state.consume_state("state-1")
    second = oauth_state.consume_state("state-1")
    assert first is not None
    assert second is None


def test_consume_expired_state_returns_none(db: Session) -> None:
    oauth_state.save_state(
        state="state-1",
        provider="oda",
        household_id="hh-1",
        initiating_user_id="user-1",
        pkce_verifier="verifier-value",
        redirect_uri="https://x/callback",
        client_id="client-abc",
        client_secret="",
        ttl_seconds=-1,  # already expired
    )

    assert oauth_state.consume_state("state-1") is None


def test_expired_state_is_still_deleted_on_consume(db: Session) -> None:
    """An expired row shouldn't be replayable just because it expired instead
    of being explicitly deleted — consume must remove it either way."""
    oauth_state.save_state(
        state="state-1",
        provider="oda",
        household_id="hh-1",
        initiating_user_id="user-1",
        pkce_verifier="verifier-value",
        redirect_uri="https://x/callback",
        client_id="client-abc",
        client_secret="",
        ttl_seconds=-1,
    )

    oauth_state.consume_state("state-1")

    from sqlmodel import select

    from app.models.cache import OAuthState

    remaining = db.exec(select(OAuthState)).all()
    assert remaining == []


def test_public_client_empty_secret_round_trips(db: Session) -> None:
    oauth_state.save_state(
        state="state-1",
        provider="oda",
        household_id="hh-1",
        initiating_user_id="user-1",
        pkce_verifier="verifier-value",
        redirect_uri="https://x/callback",
        client_id="client-abc",
        client_secret="",
    )
    row = oauth_state.consume_state("state-1")
    assert row is not None
    assert row.client_secret == ""


def test_ttl_is_respected(db: Session) -> None:
    before = datetime.utcnow()
    oauth_state.save_state(
        state="state-1",
        provider="oda",
        household_id="hh-1",
        initiating_user_id="user-1",
        pkce_verifier="verifier-value",
        redirect_uri="https://x/callback",
        client_id="client-abc",
        client_secret="",
        ttl_seconds=120,
    )

    from sqlmodel import select

    from app.models.cache import OAuthState

    row = db.exec(select(OAuthState).where(OAuthState.state == "state-1")).first()
    assert row is not None
    assert row.expires_at - before >= timedelta(seconds=119)
    assert row.expires_at - before <= timedelta(seconds=121)
