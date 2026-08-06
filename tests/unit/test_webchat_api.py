"""Unit tests for app.webchat.api's HTTP routes (login picker + session
issuance/auth). The WebSocket endpoint is intentionally not exercised here —
its message handling is already covered by test_webchat_dispatch.py and
test_webchat_channel.py; this file only checks the plain HTTP surface
(user list, session create/validate/revoke) via FastAPI's TestClient.
"""

from __future__ import annotations

from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel

import app.webchat.session as wc_session
from app.models.users import Household, User
from app.webchat.app import create_webchat_app


def _threaded_memory_engine() -> object:
    """Like conftest's in_memory_engine, but with StaticPool: TestClient runs
    the ASGI app on a background thread via anyio's portal, and a plain
    sqlite:///:memory: engine hands each thread a distinct (empty) database
    without StaticPool pinning every checkout to the same connection."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """A real (in-memory) users.db + cache.db with one seeded User, and the
    standalone web chat FastAPI app wired to them — exercises
    app.webchat.api's routes end-to-end against real SQLModel queries
    rather than a mock DB layer."""
    users_engine = _threaded_memory_engine()
    cache_engine = _threaded_memory_engine()

    @contextmanager
    def _users_session():  # type: ignore[misc]
        with Session(users_engine) as s:  # type: ignore[arg-type]
            yield s

    @contextmanager
    def _cache_session():  # type: ignore[misc]
        with Session(cache_engine) as s:  # type: ignore[arg-type]
            yield s

    monkeypatch.setattr("app.db.users_session", _users_session)
    monkeypatch.setattr(wc_session, "cache_session", _cache_session)

    with Session(users_engine) as s:  # type: ignore[arg-type]
        household = Household(id="hh-1", name="The Home")
        s.add(household)
        s.add(User(id="user-1", household_id="hh-1", telegram_id=1001, name="Kristian"))
        s.commit()

    app = create_webchat_app()
    return TestClient(app)


def test_list_users_returns_household_users(client: TestClient) -> None:
    resp = client.get("/api/users")
    assert resp.status_code == 200
    body = resp.json()
    assert body["users"] == [{"id": "user-1", "name": "Kristian", "household_id": "hh-1"}]


def test_start_session_for_known_user_returns_token(client: TestClient) -> None:
    resp = client.post("/api/session", json={"user_id": "user-1"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["user_id"] == "user-1"
    assert body["name"] == "Kristian"
    assert body["token"]


def test_start_session_for_unknown_user_returns_404(client: TestClient) -> None:
    resp = client.post("/api/session", json={"user_id": "does-not-exist"})
    assert resp.status_code == 404


def test_me_without_auth_header_is_unauthorized(client: TestClient) -> None:
    resp = client.get("/api/me")
    assert resp.status_code == 401


def test_me_with_garbage_token_is_unauthorized(client: TestClient) -> None:
    resp = client.get("/api/me", headers={"Authorization": "Bearer not-a-real-token"})
    assert resp.status_code == 401


def test_me_with_valid_session_returns_user_info(client: TestClient) -> None:
    created = client.post("/api/session", json={"user_id": "user-1"}).json()

    resp = client.get("/api/me", headers={"Authorization": f"Bearer {created['token']}"})

    assert resp.status_code == 200
    assert resp.json()["user_id"] == "user-1"


def test_delete_session_revokes_it(client: TestClient) -> None:
    created = client.post("/api/session", json={"user_id": "user-1"}).json()
    headers = {"Authorization": f"Bearer {created['token']}"}

    delete_resp = client.request("DELETE", "/api/session", headers=headers)
    assert delete_resp.status_code == 200

    me_resp = client.get("/api/me", headers=headers)
    assert me_resp.status_code == 401


def test_index_serves_chat_html(client: TestClient) -> None:
    resp = client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "HomeAgent" in resp.text
