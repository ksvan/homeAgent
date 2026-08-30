"""Unit tests for POST /admin/users/invite — admin-issued web chat
enrollment invites. See
docs/household-identity-and-access-design.md Option A.

Mirrors tests/unit/test_control_api_integrations.py's fixture approach:
real in-memory users.db/cache.db wired via monkeypatched session factories,
exercised through FastAPI's TestClient against the real admin router.
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
    monkeypatch.setattr("app.control.audit.cache_session", _cache_session)
    monkeypatch.setattr(
        "app.control.admin_events.emit_admin_event", lambda *a, **k: None, raising=False
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


def test_create_invite_returns_claim_url(client: TestClient) -> None:
    resp = client.post("/admin/users/invite", json={"user_id": "user-1"}, headers=_AUTH)
    assert resp.status_code == 200
    body = resp.json()
    assert "/invite/" in body["claim_url"]
    assert body["expires_at"]


def test_create_invite_unknown_user_returns_error(client: TestClient) -> None:
    resp = client.post("/admin/users/invite", json={"user_id": "no-such-user"}, headers=_AUTH)
    assert resp.status_code == 200
    assert "error" in resp.json()


def test_create_invite_requires_admin_auth(client: TestClient) -> None:
    resp = client.post("/admin/users/invite", json={"user_id": "user-1"})
    assert resp.status_code in (401, 403)


def test_create_invite_is_usable_via_get_valid_invite(client: TestClient) -> None:
    resp = client.post("/admin/users/invite", json={"user_id": "user-1"}, headers=_AUTH)
    claim_url = resp.json()["claim_url"]
    token = claim_url.rsplit("/invite/", 1)[1]

    from app.webchat.invites import get_valid_invite

    invite = get_valid_invite(token)
    assert invite is not None
    assert invite.user_id == "user-1"
    assert invite.household_id == "hh-1"
