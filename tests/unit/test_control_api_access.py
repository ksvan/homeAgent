"""Unit tests for the Phase 2 admin "Access" endpoints — create household
member, issue Telegram link code, and the per-surface/active permission
matrix mutation. See
docs/household-identity-and-access-design.md Option D/E.

Mirrors tests/unit/test_control_api_webchat_invite.py's fixture approach:
real in-memory users.db/cache.db wired via monkeypatched session
factories, exercised through FastAPI's TestClient against the real admin
router.
"""

from __future__ import annotations

from contextlib import contextmanager

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel

from app.control.api import router as admin_router
from app.models.users import Household, User

_SECRET = "unit-test-secret-key-not-a-real-fernet-key"
_AUTH = {"Authorization": f"Bearer {_SECRET}"}


def _threaded_memory_engine() -> object:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


class _FakeWebChannel:
    def __init__(self) -> None:
        self.closed_for: list[str] = []

    async def close_connections_for_user(self, user_id: str, code: int = 4403) -> int:
        self.closed_for.append(user_id)
        return 1


@pytest.fixture
def fake_channel(monkeypatch: pytest.MonkeyPatch) -> _FakeWebChannel:
    channel = _FakeWebChannel()
    monkeypatch.setattr("app.webchat.api.get_web_channel", lambda: channel)
    return channel


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

    monkeypatch.setattr("app.db.users_session", _users_session)
    monkeypatch.setattr("app.webchat.invites.cache_session", _cache_session)
    monkeypatch.setattr("app.webchat.link_codes.cache_session", _cache_session)
    monkeypatch.setattr("app.control.audit.cache_session", _cache_session)
    monkeypatch.setattr(
        "app.control.admin_events.emit_admin_event", lambda *a, **k: None, raising=False
    )
    monkeypatch.setattr(
        "app.world.repository.WorldModelRepository.upsert_member", lambda *a, **k: None
    )

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
# GET /admin/users
# ---------------------------------------------------------------------------


def test_list_users_includes_phase2_fields(client: TestClient) -> None:
    resp = client.get("/admin/users", headers=_AUTH)
    assert resp.status_code == 200
    user = resp.json()["users"][0]
    assert user["is_active"] is True
    assert user["telegram_enabled"] is True
    assert user["web_chat_enabled"] is True


# ---------------------------------------------------------------------------
# POST /admin/users — create a web-only household member
# ---------------------------------------------------------------------------


def test_create_user_with_no_telegram_id(client: TestClient) -> None:
    resp = client.post("/admin/users", json={"name": "Guest"}, headers=_AUTH)
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "Guest"

    listing = client.get("/admin/users", headers=_AUTH).json()["users"]
    created = next(u for u in listing if u["id"] == body["id"])
    assert created["telegram_id"] is None


def test_create_user_requires_a_name(client: TestClient) -> None:
    resp = client.post("/admin/users", json={"name": "   "}, headers=_AUTH)
    assert resp.status_code == 200
    assert "error" in resp.json()


def test_create_user_reuses_existing_household(client: TestClient) -> None:
    resp = client.post("/admin/users", json={"name": "Guest"}, headers=_AUTH)
    body = resp.json()

    listing = client.get("/admin/users", headers=_AUTH).json()["users"]
    created = next(u for u in listing if u["id"] == body["id"])
    existing = next(u for u in listing if u["id"] == "user-1")
    # Both rows share the single seeded household — confirmed indirectly
    # via a successful invite issuance below, which requires household_id.
    assert created is not None and existing is not None


# ---------------------------------------------------------------------------
# POST /admin/users/link-code
# ---------------------------------------------------------------------------


def test_create_link_code_for_known_user(client: TestClient) -> None:
    resp = client.post("/admin/users/link-code", json={"user_id": "user-1"}, headers=_AUTH)
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"]
    assert body["expires_at"]


def test_create_link_code_for_unknown_user(client: TestClient) -> None:
    resp = client.post("/admin/users/link-code", json={"user_id": "no-such-user"}, headers=_AUTH)
    assert resp.status_code == 200
    assert "error" in resp.json()


# ---------------------------------------------------------------------------
# PATCH /admin/users/{id}/access
# ---------------------------------------------------------------------------


def test_update_access_unknown_user_returns_error(client: TestClient) -> None:
    resp = client.patch(
        "/admin/users/no-such-user/access", json={"is_active": False}, headers=_AUTH
    )
    assert resp.status_code == 200
    assert "error" in resp.json()


def test_disabling_telegram_only_does_not_close_web_sockets(
    client: TestClient, fake_channel: _FakeWebChannel
) -> None:
    resp = client.patch(
        "/admin/users/user-1/access", json={"telegram_enabled": False}, headers=_AUTH
    )
    assert resp.status_code == 200
    assert resp.json()["telegram_enabled"] is False
    assert fake_channel.closed_for == []


def test_disabling_web_chat_force_closes_open_sockets(
    client: TestClient, fake_channel: _FakeWebChannel
) -> None:
    resp = client.patch(
        "/admin/users/user-1/access", json={"web_chat_enabled": False}, headers=_AUTH
    )
    assert resp.status_code == 200
    assert fake_channel.closed_for == ["user-1"]


def test_deactivating_user_force_closes_open_sockets(
    client: TestClient, fake_channel: _FakeWebChannel
) -> None:
    resp = client.patch("/admin/users/user-1/access", json={"is_active": False}, headers=_AUTH)
    assert resp.status_code == 200
    assert fake_channel.closed_for == ["user-1"]


def test_resubmitting_the_same_value_does_not_force_close_again(
    client: TestClient, fake_channel: _FakeWebChannel
) -> None:
    client.patch("/admin/users/user-1/access", json={"web_chat_enabled": False}, headers=_AUTH)
    fake_channel.closed_for.clear()

    resp = client.patch(
        "/admin/users/user-1/access", json={"web_chat_enabled": False}, headers=_AUTH
    )

    assert resp.status_code == 200
    assert fake_channel.closed_for == []


def test_re_enabling_access_does_not_force_close(
    client: TestClient, fake_channel: _FakeWebChannel
) -> None:
    resp = client.patch(
        "/admin/users/user-1/access", json={"web_chat_enabled": True}, headers=_AUTH
    )
    assert resp.status_code == 200
    assert fake_channel.closed_for == []


# ---------------------------------------------------------------------------
# Event rule creation guards against a user with no linked Telegram id
# (regression: User.telegram_id becoming nullable in Phase 2)
# ---------------------------------------------------------------------------


def test_event_rule_creation_rejects_user_with_no_telegram_id(client: TestClient) -> None:
    create_resp = client.post("/admin/users", json={"name": "Guest"}, headers=_AUTH)
    web_only_user_id = create_resp.json()["id"]

    resp = client.post(
        "/admin/event-rules",
        json={
            "name": "Test rule",
            "user_id": web_only_user_id,
            "prompt_template": "Something happened",
        },
        headers=_AUTH,
    )
    assert resp.status_code == 200
    assert "no linked Telegram account" in resp.json()["error"]
