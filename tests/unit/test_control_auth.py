"""Unit tests for app.control.auth.require_admin_auth — the dual-path
admin auth dependency (shared-secret break-glass + WebAuthn passkey
session) added in Phase 4. See
docs/household-identity-and-access-design.md.

Exercised through a minimal standalone FastAPI app with GET/POST/DELETE
probe routes, rather than calling the dependency function directly, so
the CSRF-only-on-mutation and method-based break-glass-audit logic (both
keyed off the real Request) are tested the same way production traffic
hits them.
"""

from __future__ import annotations

from contextlib import contextmanager

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel

from app.control.auth import require_admin_auth
from app.models.users import Household, User

_SECRET = "unit-test-secret-key-not-a-real-fernet-key"


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
    monkeypatch.setattr("app.webchat.session.cache_session", _cache_session)
    monkeypatch.setattr("app.control.audit.cache_session", _cache_session)
    monkeypatch.setattr(
        "app.control.admin_events.emit_admin_event", lambda *a, **k: None, raising=False
    )

    with Session(users_engine) as s:  # type: ignore[arg-type]
        s.add(Household(id="hh-1", name="The Home"))
        s.add(User(id="admin-1", household_id="hh-1", telegram_id=1, name="Admin", is_admin=True))
        s.add(
            User(
                id="nonadmin-1",
                household_id="hh-1",
                telegram_id=2,
                name="NotAdmin",
                is_admin=False,
            )
        )
        s.commit()

    yield users_engine, cache_engine
    get_settings.cache_clear()


@pytest.fixture
def client(engines: tuple[object, object]) -> TestClient:
    app = FastAPI()

    @app.get("/probe", dependencies=[Depends(require_admin_auth)])
    async def probe_get() -> dict[str, bool]:
        return {"ok": True}

    @app.post("/probe", dependencies=[Depends(require_admin_auth)])
    async def probe_post() -> dict[str, bool]:
        return {"ok": True}

    @app.delete("/probe", dependencies=[Depends(require_admin_auth)])
    async def probe_delete() -> dict[str, bool]:
        return {"ok": True}

    return TestClient(app)


def _session_for(user_id: str) -> tuple[str, str]:
    from app.webchat.session import create_session

    info = create_session(user_id, "hh-1")
    return info.token, "csrf-token-value"


# ---------------------------------------------------------------------------
# Break-glass (shared secret)
# ---------------------------------------------------------------------------


def test_valid_bearer_token_is_accepted(client: TestClient) -> None:
    resp = client.get("/probe", headers={"Authorization": f"Bearer {_SECRET}"})
    assert resp.status_code == 200


def test_valid_query_token_is_accepted(client: TestClient) -> None:
    resp = client.get(f"/probe?token={_SECRET}")
    assert resp.status_code == 200


def test_wrong_bearer_token_is_rejected(client: TestClient) -> None:
    resp = client.get("/probe", headers={"Authorization": "Bearer wrong"})
    assert resp.status_code == 401


def test_missing_credentials_is_rejected(client: TestClient) -> None:
    resp = client.get("/probe")
    assert resp.status_code == 401


def test_break_glass_mutation_is_durably_audited(client: TestClient) -> None:
    resp = client.post("/probe", headers={"Authorization": f"Bearer {_SECRET}"})
    assert resp.status_code == 200

    from sqlmodel import select

    from app.control.audit import cache_session
    from app.models.cache import AuditLog

    with cache_session() as db:
        rows = db.exec(
            select(AuditLog).where(AuditLog.event_type == "admin.break_glass_used")
        ).all()
    assert len(rows) == 1


def test_break_glass_get_is_not_audited(client: TestClient) -> None:
    resp = client.get("/probe", headers={"Authorization": f"Bearer {_SECRET}"})
    assert resp.status_code == 200

    from sqlmodel import select

    from app.control.audit import cache_session
    from app.models.cache import AuditLog

    with cache_session() as db:
        rows = db.exec(
            select(AuditLog).where(AuditLog.event_type == "admin.break_glass_used")
        ).all()
    assert len(rows) == 0


# ---------------------------------------------------------------------------
# Passkey session
# ---------------------------------------------------------------------------


def test_admin_session_cookie_is_accepted_for_get(client: TestClient) -> None:
    token, _ = _session_for("admin-1")
    resp = client.get("/probe", cookies={"hac_session": token})
    assert resp.status_code == 200


def test_non_admin_session_cookie_is_rejected(client: TestClient) -> None:
    token, _ = _session_for("nonadmin-1")
    resp = client.get("/probe", cookies={"hac_session": token})
    assert resp.status_code == 401


def test_unknown_session_token_is_rejected(client: TestClient) -> None:
    resp = client.get("/probe", cookies={"hac_session": "not-a-real-token"})
    assert resp.status_code == 401


def test_admin_session_mutation_requires_csrf(client: TestClient) -> None:
    token, _ = _session_for("admin-1")
    resp = client.post("/probe", cookies={"hac_session": token})
    assert resp.status_code == 403


def test_admin_session_mutation_with_mismatched_csrf_is_rejected(client: TestClient) -> None:
    token, _ = _session_for("admin-1")
    resp = client.post(
        "/probe",
        cookies={"hac_session": token, "hac_csrf": "real-value"},
        headers={"X-CSRF-Token": "wrong-value"},
    )
    assert resp.status_code == 403


def test_admin_session_mutation_with_matching_csrf_succeeds(client: TestClient) -> None:
    token, _ = _session_for("admin-1")
    resp = client.post(
        "/probe",
        cookies={"hac_session": token, "hac_csrf": "matching-value"},
        headers={"X-CSRF-Token": "matching-value"},
    )
    assert resp.status_code == 200


def test_admin_session_get_does_not_require_csrf(client: TestClient) -> None:
    token, _ = _session_for("admin-1")
    resp = client.get("/probe", cookies={"hac_session": token})
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Dev mode (no APP_SECRET_KEY configured) — unchanged pre-Phase-4 behavior
# ---------------------------------------------------------------------------


def test_dev_mode_is_open_when_no_secret_configured(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "app_secret_key", "")
    resp = client.get("/probe")
    assert resp.status_code == 200
