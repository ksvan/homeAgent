"""Unit tests for the public /integrations/{provider}/callback route
(app/api/integrations.py) — the OAuth redirect target Oda's consent screen
sends the household member's browser back to.

Exercises the route end-to-end via FastAPI's TestClient against real
in-memory users.db/cache.db, mirroring test_control_api_integrations.py.
All Oda network calls are monkeypatched — nothing here hits oda.com.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel

from app.api.integrations import router as integrations_router
from app.integrations import oauth_state
from app.models.users import Household, User
from app.oda.oauth import OAuthServerMetadata, OdaOAuthError, TokenResponse

_SECRET = "unit-test-secret-key-not-a-real-fernet-key"

_METADATA = OAuthServerMetadata(
    authorization_endpoint="https://oda.com/o/authorize/",
    token_endpoint="https://oda.com/o/token/",
    revocation_endpoint="https://oda.com/o/revoke_token/",
    registration_endpoint="https://oda.com/o/register/",
)


def _threaded_memory_engine() -> object:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


@pytest.fixture
def engines(monkeypatch: pytest.MonkeyPatch) -> tuple[object, object]:
    from app.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("APP_SECRET_KEY", _SECRET)

    users_engine = _threaded_memory_engine()
    cache_engine = _threaded_memory_engine()

    @contextmanager  # type: ignore[misc]
    def _users_session():
        with Session(users_engine) as s:  # type: ignore[arg-type]
            yield s

    @contextmanager  # type: ignore[misc]
    def _cache_session():
        with Session(cache_engine) as s:  # type: ignore[arg-type]
            yield s

    monkeypatch.setattr("app.integrations.accounts.users_session", _users_session)
    monkeypatch.setattr("app.integrations.oauth_state.cache_session", _cache_session)
    import app.integrations.accounts as accounts_mod

    monkeypatch.setattr(accounts_mod, "_refresh_locks", {})

    with Session(users_engine) as s:  # type: ignore[arg-type]
        s.add(Household(id="hh-1", name="The Home"))
        s.add(User(id="user-1", household_id="hh-1", telegram_id=1, name="Kristian"))
        s.commit()

    yield users_engine, cache_engine
    get_settings.cache_clear()


@pytest.fixture
def client(engines: tuple[object, object]) -> TestClient:
    app = FastAPI()
    app.include_router(integrations_router)
    with TestClient(app) as c:
        yield c


def _seed_state(**overrides: object) -> None:
    defaults: dict[str, object] = dict(
        state="state-abc",
        provider="oda",
        household_id="hh-1",
        initiating_user_id="user-1",
        pkce_verifier="verifier-value",
        redirect_uri="https://home.example.com/integrations/oda/callback",
        client_id="client-abc",
        client_secret="client-secret-xyz",
    )
    defaults.update(overrides)
    oauth_state.save_state(**defaults)  # type: ignore[arg-type]


def test_unknown_provider_returns_error_page(client: TestClient) -> None:
    resp = client.get("/integrations/nope/callback?code=x&state=y")
    assert resp.status_code == 400
    assert "Unknown integration provider" in resp.text


def test_missing_code_and_state_returns_error_page(client: TestClient) -> None:
    resp = client.get("/integrations/oda/callback")
    assert resp.status_code == 400
    assert "invalid, expired, or was already used" in resp.text


def test_unknown_state_returns_error_page(client: TestClient) -> None:
    resp = client.get("/integrations/oda/callback?code=auth-code&state=nonexistent")
    assert resp.status_code == 400
    assert "invalid, expired, or was already used" in resp.text


def test_error_param_returns_denied_page_and_consumes_state(
    client: TestClient, engines: tuple[object, object]
) -> None:
    _seed_state()
    resp = client.get("/integrations/oda/callback?state=state-abc&error=access_denied")
    assert resp.status_code == 400
    assert "cancelled or denied" in resp.text

    # State must be consumed even on a denied consent, not left dangling.
    assert oauth_state.consume_state("state-abc") is None


def test_expired_state_returns_error_page(client: TestClient) -> None:
    _seed_state(ttl_seconds=-1)
    resp = client.get("/integrations/oda/callback?code=auth-code&state=state-abc")
    assert resp.status_code == 400
    assert "invalid, expired, or was already used" in resp.text


def test_successful_exchange_creates_account_and_returns_success_page(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, engines: tuple[object, object]
) -> None:
    _seed_state()

    async def _fake_discover() -> OAuthServerMetadata:
        return _METADATA

    exchange_calls: list[dict[str, object]] = []

    async def _fake_exchange(metadata: object, **kwargs: object) -> TokenResponse:
        exchange_calls.append(kwargs)
        return TokenResponse(
            access_token="new-access-token",
            refresh_token="new-refresh-token",
            expires_at=datetime.utcnow() + timedelta(hours=1),
        )

    monkeypatch.setattr("app.oda.oauth.discover_metadata", _fake_discover)
    monkeypatch.setattr("app.oda.oauth.exchange_code", _fake_exchange)

    resp = client.get("/integrations/oda/callback?code=auth-code-123&state=state-abc")
    assert resp.status_code == 200
    assert "connected" in resp.text.lower()

    assert len(exchange_calls) == 1
    assert exchange_calls[0]["code"] == "auth-code-123"
    assert exchange_calls[0]["client_id"] == "client-abc"
    assert exchange_calls[0]["client_secret"] == "client-secret-xyz"
    assert exchange_calls[0]["code_verifier"] == "verifier-value"
    assert exchange_calls[0]["redirect_uri"] == "https://home.example.com/integrations/oda/callback"

    from app.integrations.accounts import get_account
    from app.integrations.crypto import decrypt

    account = get_account("hh-1", "oda")
    assert account is not None
    assert account.connected_by_user_id == "user-1"
    assert decrypt(account.access_token) == "new-access-token"
    assert decrypt(account.refresh_token) == "new-refresh-token"


def test_successful_exchange_restarts_mcp_and_reloads_agent_when_feature_enabled(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, engines: tuple[object, object]
) -> None:
    from app.config import get_settings

    monkeypatch.setenv("FEATURE_ODA", "true")
    get_settings.cache_clear()
    _seed_state()

    async def _fake_discover() -> OAuthServerMetadata:
        return _METADATA

    async def _fake_exchange(metadata: object, **kwargs: object) -> TokenResponse:
        return TokenResponse(
            access_token="at", refresh_token="rt", expires_at=datetime.utcnow() + timedelta(hours=1)
        )

    calls: list[str] = []

    async def _fake_stop_mcp() -> None:
        calls.append("stop_mcp")

    async def _fake_start_mcp() -> None:
        calls.append("start_mcp")

    def _fake_reload_agent() -> None:
        calls.append("reload_agent")

    monkeypatch.setattr("app.oda.oauth.discover_metadata", _fake_discover)
    monkeypatch.setattr("app.oda.oauth.exchange_code", _fake_exchange)
    monkeypatch.setattr("app.oda.mcp_client.stop_mcp", _fake_stop_mcp)
    monkeypatch.setattr("app.oda.mcp_client.start_mcp", _fake_start_mcp)
    monkeypatch.setattr("app.agent.agent.reload_agent", _fake_reload_agent)

    resp = client.get("/integrations/oda/callback?code=auth-code&state=state-abc")
    assert resp.status_code == 200
    assert calls == ["stop_mcp", "start_mcp", "reload_agent"]
    get_settings.cache_clear()


def test_successful_exchange_skips_mcp_lifecycle_when_feature_disabled(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, engines: tuple[object, object]
) -> None:
    # FEATURE_ODA defaults to false.
    _seed_state()

    async def _fake_discover() -> OAuthServerMetadata:
        return _METADATA

    async def _fake_exchange(metadata: object, **kwargs: object) -> TokenResponse:
        return TokenResponse(
            access_token="at", refresh_token="rt", expires_at=datetime.utcnow() + timedelta(hours=1)
        )

    async def _fail_start_mcp() -> None:
        raise AssertionError("start_mcp must not run when FEATURE_ODA is false")

    monkeypatch.setattr("app.oda.oauth.discover_metadata", _fake_discover)
    monkeypatch.setattr("app.oda.oauth.exchange_code", _fake_exchange)
    monkeypatch.setattr("app.oda.mcp_client.start_mcp", _fail_start_mcp)

    resp = client.get("/integrations/oda/callback?code=auth-code&state=state-abc")
    assert resp.status_code == 200


def test_state_is_not_reusable(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, engines: tuple[object, object]
) -> None:
    _seed_state()

    async def _fake_discover() -> OAuthServerMetadata:
        return _METADATA

    async def _fake_exchange(metadata: object, **kwargs: object) -> TokenResponse:
        return TokenResponse(
            access_token="at", refresh_token="rt", expires_at=datetime.utcnow() + timedelta(hours=1)
        )

    monkeypatch.setattr("app.oda.oauth.discover_metadata", _fake_discover)
    monkeypatch.setattr("app.oda.oauth.exchange_code", _fake_exchange)

    first = client.get("/integrations/oda/callback?code=auth-code&state=state-abc")
    second = client.get("/integrations/oda/callback?code=auth-code&state=state-abc")
    assert first.status_code == 200
    assert second.status_code == 400
    assert "invalid, expired, or was already used" in second.text


def test_token_exchange_failure_returns_error_page_without_creating_account(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, engines: tuple[object, object]
) -> None:
    _seed_state()

    async def _fake_discover() -> OAuthServerMetadata:
        return _METADATA

    async def _fake_exchange_fails(metadata: object, **kwargs: object) -> TokenResponse:
        raise OdaOAuthError("token endpoint rejected the code")

    monkeypatch.setattr("app.oda.oauth.discover_metadata", _fake_discover)
    monkeypatch.setattr("app.oda.oauth.exchange_code", _fake_exchange_fails)

    resp = client.get("/integrations/oda/callback?code=auth-code&state=state-abc")
    assert resp.status_code == 400
    assert "Could not complete the connection" in resp.text

    from app.integrations.accounts import get_account

    assert get_account("hh-1", "oda") is None
