"""Unit tests for app.control.api's /admin/integrations routes (list,
connect, disconnect) — see docs/oda-grocery-mcp-tool-design.md "New: admin
Integrations page".

Exercises the real routes end-to-end via FastAPI's TestClient against a real
(in-memory) users.db + cache.db, mirroring tests/unit/test_webchat_api.py's
approach. All calls to Oda's actual OAuth endpoints are monkeypatched —
nothing here hits oda.com.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, select

from app.control.api import router as admin_router
from app.models.users import Household, User
from app.oda.oauth import OAuthServerMetadata, OdaOAuthError

_SECRET = "unit-test-secret-key-not-a-real-fernet-key"
_AUTH = {"Authorization": f"Bearer {_SECRET}"}

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
    """Wire real in-memory users.db/cache.db engines into every module that
    imports a session factory at module scope, plus seed a household + user."""
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

    monkeypatch.setattr("app.db.users_session", _users_session)
    monkeypatch.setattr("app.integrations.accounts.users_session", _users_session)
    monkeypatch.setattr("app.integrations.oauth_state.cache_session", _cache_session)
    # accounts._refresh_locks is module-level state shared across tests otherwise.
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
    app = FastAPI(docs_url=None, redoc_url=None)
    app.include_router(admin_router)
    with TestClient(app) as c:
        yield c


# ---------------------------------------------------------------------------
# GET /admin/integrations
# ---------------------------------------------------------------------------


def test_list_shows_not_connected_by_default(client: TestClient) -> None:
    resp = client.get("/admin/integrations", headers=_AUTH)
    assert resp.status_code == 200
    body = resp.json()
    assert body["integrations"] == [
        {
            "provider": "oda",
            "display_name": "Oda",
            "connected": False,
            "connected_by": None,
            "connected_at": None,
            "expires_at": None,
        }
    ]


def test_list_shows_connected_with_user_name(client: TestClient) -> None:
    from app.integrations.accounts import upsert_account

    upsert_account(
        household_id="hh-1",
        provider="oda",
        connected_by_user_id="user-1",
        client_id="client-abc",
        client_secret="",
        access_token="at",
        refresh_token="rt",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )

    resp = client.get("/admin/integrations", headers=_AUTH)
    body = resp.json()
    assert body["integrations"][0]["connected"] is True
    assert body["integrations"][0]["connected_by"] == "Kristian"
    assert body["integrations"][0]["connected_at"] is not None


def test_list_requires_auth(client: TestClient) -> None:
    resp = client.get("/admin/integrations")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# POST /admin/integrations/{provider}/connect
# ---------------------------------------------------------------------------


def test_connect_unknown_provider_returns_error(client: TestClient) -> None:
    resp = client.post(
        "/admin/integrations/nope/connect", headers=_AUTH, json={"user_id": "user-1"}
    )
    assert resp.status_code == 200
    assert "Unknown integration provider" in resp.json()["error"]


def test_connect_returns_error_when_feature_oda_disabled(client: TestClient) -> None:
    # FEATURE_ODA defaults to false — this must be checked before anything
    # else (public base URL, DCR, ...).
    resp = client.post("/admin/integrations/oda/connect", headers=_AUTH, json={"user_id": "user-1"})
    assert resp.json()["error"] == "Oda integration is disabled (FEATURE_ODA=false)"


def test_connect_without_public_base_url_returns_error(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.config import get_settings

    monkeypatch.setenv("FEATURE_ODA", "true")
    get_settings.cache_clear()

    resp = client.post("/admin/integrations/oda/connect", headers=_AUTH, json={"user_id": "user-1"})
    assert resp.json()["error"] == "ODA_OAUTH_PUBLIC_BASE_URL is not configured"


def test_connect_success_returns_authorize_url_and_saves_state(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, engines: tuple[object, object]
) -> None:
    from app.config import get_settings

    monkeypatch.setenv("FEATURE_ODA", "true")
    monkeypatch.setenv("ODA_OAUTH_PUBLIC_BASE_URL", "https://home.example.com")
    get_settings.cache_clear()

    async def _fake_discover() -> OAuthServerMetadata:
        return _METADATA

    async def _fake_register(metadata: object, redirect_uri: str) -> tuple[str, str]:
        assert redirect_uri == "https://home.example.com/integrations/oda/callback"
        return "client-abc", "client-secret-xyz"

    monkeypatch.setattr("app.oda.oauth.discover_metadata", _fake_discover)
    monkeypatch.setattr("app.oda.oauth.register_client", _fake_register)

    resp = client.post("/admin/integrations/oda/connect", headers=_AUTH, json={"user_id": "user-1"})
    body = resp.json()
    assert "authorize_url" in body
    assert body["authorize_url"].startswith("https://oda.com/o/authorize/?")
    assert "client_id=client-abc" in body["authorize_url"]

    _users_engine, cache_engine = engines
    with Session(cache_engine) as s:  # type: ignore[arg-type]
        from app.models.cache import OAuthState

        rows = s.exec(select(OAuthState)).all()
    assert len(rows) == 1
    assert rows[0].household_id == "hh-1"
    assert rows[0].initiating_user_id == "user-1"
    assert rows[0].client_id == "client-abc"
    assert rows[0].redirect_uri == "https://home.example.com/integrations/oda/callback"


def test_connect_discovery_failure_returns_error(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.config import get_settings

    monkeypatch.setenv("FEATURE_ODA", "true")
    monkeypatch.setenv("ODA_OAUTH_PUBLIC_BASE_URL", "https://home.example.com")
    get_settings.cache_clear()

    async def _fake_discover_fails() -> OAuthServerMetadata:
        raise OdaOAuthError("Oda is down")

    monkeypatch.setattr("app.oda.oauth.discover_metadata", _fake_discover_fails)

    resp = client.post("/admin/integrations/oda/connect", headers=_AUTH, json={"user_id": "user-1"})
    assert "Could not start the Oda connection" in resp.json()["error"]


# ---------------------------------------------------------------------------
# POST /admin/integrations/{provider}/disconnect
# ---------------------------------------------------------------------------


def test_disconnect_when_not_connected_does_not_hit_network(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _fail(*args: object, **kwargs: object) -> object:
        raise AssertionError("should not be called when nothing is connected")

    monkeypatch.setattr("app.oda.oauth.discover_metadata", _fail)

    resp = client.post("/admin/integrations/oda/disconnect", headers=_AUTH)
    assert resp.json() == {"disconnected": True}


def test_disconnect_unknown_provider_returns_error(client: TestClient) -> None:
    resp = client.post("/admin/integrations/nope/disconnect", headers=_AUTH)
    assert "Unknown integration provider" in resp.json()["error"]


def test_disconnect_revokes_and_deletes_account(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, engines: tuple[object, object]
) -> None:
    from app.integrations.accounts import upsert_account

    upsert_account(
        household_id="hh-1",
        provider="oda",
        connected_by_user_id="user-1",
        client_id="client-abc",
        client_secret="client-secret-xyz",
        access_token="at",
        refresh_token="the-refresh-token",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )

    revoke_calls: list[dict[str, object]] = []

    async def _fake_discover() -> OAuthServerMetadata:
        return _METADATA

    async def _fake_revoke(metadata: object, **kwargs: object) -> None:
        revoke_calls.append(kwargs)

    monkeypatch.setattr("app.oda.oauth.discover_metadata", _fake_discover)
    monkeypatch.setattr("app.oda.oauth.revoke_token", _fake_revoke)

    resp = client.post("/admin/integrations/oda/disconnect", headers=_AUTH)
    assert resp.json() == {"disconnected": True}

    assert len(revoke_calls) == 1
    assert revoke_calls[0]["token"] == "the-refresh-token"
    assert revoke_calls[0]["client_id"] == "client-abc"
    assert revoke_calls[0]["client_secret"] == "client-secret-xyz"

    from app.integrations.accounts import get_account

    assert get_account("hh-1", "oda") is None


def test_disconnect_still_deletes_account_when_revoke_fails(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.integrations.accounts import get_account, upsert_account

    upsert_account(
        household_id="hh-1",
        provider="oda",
        connected_by_user_id="user-1",
        client_id="client-abc",
        client_secret="",
        access_token="at",
        refresh_token="rt",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )

    async def _fake_discover() -> OAuthServerMetadata:
        return _METADATA

    async def _fake_revoke_fails(metadata: object, **kwargs: object) -> None:
        raise OdaOAuthError("revoke failed")

    monkeypatch.setattr("app.oda.oauth.discover_metadata", _fake_discover)
    monkeypatch.setattr("app.oda.oauth.revoke_token", _fake_revoke_fails)

    resp = client.post("/admin/integrations/oda/disconnect", headers=_AUTH)
    assert resp.json() == {"disconnected": True}
    assert get_account("hh-1", "oda") is None


def test_disconnect_stops_mcp_and_reloads_agent(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.integrations.accounts import upsert_account

    upsert_account(
        household_id="hh-1",
        provider="oda",
        connected_by_user_id="user-1",
        client_id="client-abc",
        client_secret="",
        access_token="at",
        refresh_token="rt",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )

    async def _fake_discover() -> OAuthServerMetadata:
        return _METADATA

    async def _fake_revoke(metadata: object, **kwargs: object) -> None:
        return None

    calls: list[str] = []

    async def _fake_stop_mcp() -> None:
        calls.append("stop_mcp")

    def _fake_reload_agent() -> None:
        calls.append("reload_agent")

    monkeypatch.setattr("app.oda.oauth.discover_metadata", _fake_discover)
    monkeypatch.setattr("app.oda.oauth.revoke_token", _fake_revoke)
    monkeypatch.setattr("app.oda.mcp_client.stop_mcp", _fake_stop_mcp)
    monkeypatch.setattr("app.agent.agent.reload_agent", _fake_reload_agent)

    resp = client.post("/admin/integrations/oda/disconnect", headers=_AUTH)
    assert resp.json() == {"disconnected": True}
    assert calls == ["stop_mcp", "reload_agent"]
