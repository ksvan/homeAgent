"""Integration tests for the Phase 4 admin passkey login endpoints
(POST /admin/auth/login/options, POST /admin/auth/login/verify,
GET /admin/auth/me, DELETE /admin/auth/session) — see
docs/household-identity-and-access-design.md.

Mirrors tests/unit/test_webchat_api_webauthn.py's approach: real
in-memory users.db/cache.db, only the WebAuthn library's own
cryptographic verification mocked.
"""

from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel

import app.bot as bot_module
import app.webchat.webauthn as wa
from app.control.api import router as admin_router
from app.models.users import Household, User, WebAuthnCredential

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


@pytest.fixture(autouse=True)
def clear_preauth_rate_limit() -> None:
    bot_module._user_call_times.clear()


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
    monkeypatch.setattr(wa, "cache_session", _cache_session)
    monkeypatch.setattr(wa, "users_session", _users_session)
    monkeypatch.setattr("app.control.audit.cache_session", _cache_session)
    monkeypatch.setattr("app.webchat.invites.cache_session", _cache_session)
    monkeypatch.setattr("app.webchat.link_codes.cache_session", _cache_session)
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
        s.add(
            WebAuthnCredential(
                user_id="admin-1",
                credential_id="admin-cred",
                public_key=wa._b64_no_pad(b"pubkey"),
                sign_count=0,
            )
        )
        s.add(
            WebAuthnCredential(
                user_id="nonadmin-1",
                credential_id="nonadmin-cred",
                public_key=wa._b64_no_pad(b"pubkey2"),
                sign_count=0,
            )
        )
        s.commit()

    yield users_engine, cache_engine
    get_settings.cache_clear()


@pytest.fixture
def client(engines: tuple[object, object]) -> TestClient:
    app = FastAPI(docs_url=None, redoc_url=None)
    app.include_router(admin_router)
    return TestClient(app)


def _mock_login(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_result = SimpleNamespace(new_sign_count=0)
    monkeypatch.setattr(wa.webauthn, "verify_authentication_response", lambda **kwargs: fake_result)


def test_admin_webauthn_common_js_served(client: TestClient) -> None:
    resp = client.get("/admin/webauthn-common.js")
    assert resp.status_code == 200
    assert "javascript" in resp.headers["content-type"]


def test_login_options_returns_challenge(client: TestClient) -> None:
    resp = client.post("/admin/auth/login/options")
    assert resp.status_code == 200
    body = resp.json()
    assert body["challenge_id"]
    assert not body["options"].get("allowCredentials")


def test_login_verify_succeeds_for_admin(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _mock_login(monkeypatch)
    options = client.post("/admin/auth/login/options").json()
    resp = client.post(
        "/admin/auth/login/verify",
        json={"challenge_id": options["challenge_id"], "credential": {"id": "admin-cred"}},
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "Admin"
    assert "hac_session" in resp.cookies
    assert "hac_csrf" in resp.cookies


def test_login_verify_denies_non_admin(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_login(monkeypatch)
    options = client.post("/admin/auth/login/options").json()
    resp = client.post(
        "/admin/auth/login/verify",
        json={"challenge_id": options["challenge_id"], "credential": {"id": "nonadmin-cred"}},
    )
    assert resp.status_code == 403


def test_login_verify_rejects_unknown_credential(client: TestClient) -> None:
    options = client.post("/admin/auth/login/options").json()
    resp = client.post(
        "/admin/auth/login/verify",
        json={"challenge_id": options["challenge_id"], "credential": {"id": "no-such-cred"}},
    )
    assert resp.status_code == 401


def test_me_requires_session(client: TestClient) -> None:
    resp = client.get("/admin/auth/me")
    assert resp.status_code == 401


def test_me_returns_identity_after_login(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _mock_login(monkeypatch)
    options = client.post("/admin/auth/login/options").json()
    login = client.post(
        "/admin/auth/login/verify",
        json={"challenge_id": options["challenge_id"], "credential": {"id": "admin-cred"}},
    )
    resp = client.get("/admin/auth/me", cookies={"hac_session": login.cookies["hac_session"]})
    assert resp.status_code == 200
    assert resp.json()["name"] == "Admin"


def test_logout_requires_csrf(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_login(monkeypatch)
    options = client.post("/admin/auth/login/options").json()
    login = client.post(
        "/admin/auth/login/verify",
        json={"challenge_id": options["challenge_id"], "credential": {"id": "admin-cred"}},
    )
    resp = client.request(
        "DELETE",
        "/admin/auth/session",
        cookies={
            "hac_session": login.cookies["hac_session"],
            "hac_csrf": login.cookies["hac_csrf"],
        },
    )
    assert resp.status_code == 403


def test_logout_revokes_session(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_login(monkeypatch)
    options = client.post("/admin/auth/login/options").json()
    login = client.post(
        "/admin/auth/login/verify",
        json={"challenge_id": options["challenge_id"], "credential": {"id": "admin-cred"}},
    )
    csrf = login.cookies["hac_csrf"]
    resp = client.request(
        "DELETE",
        "/admin/auth/session",
        cookies={"hac_session": login.cookies["hac_session"], "hac_csrf": csrf},
        headers={"X-CSRF-Token": csrf},
    )
    assert resp.status_code == 200

    follow_up = client.get("/admin/auth/me", cookies={"hac_session": login.cookies["hac_session"]})
    assert follow_up.status_code == 401


def test_break_glass_still_works_after_admin_passkey_endpoints_added(client: TestClient) -> None:
    """The existing shared-secret path must keep working unchanged
    alongside the new passkey endpoints."""
    resp = client.get("/admin/users", headers=_AUTH)
    assert resp.status_code == 200


def _login_as_admin(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    _mock_login(monkeypatch)
    options = client.post("/admin/auth/login/options").json()
    resp = client.post(
        "/admin/auth/login/verify",
        json={"challenge_id": options["challenge_id"], "credential": {"id": "admin-cred"}},
    )
    return {"hac_session": resp.cookies["hac_session"], "hac_csrf": resp.cookies["hac_csrf"]}


# ---------------------------------------------------------------------------
# BR-05 (2026-08-31 security re-review): privileged actions are now
# attributable to the real admin's user_id when authenticated via passkey,
# not just the shared "admin" marker.
# ---------------------------------------------------------------------------


def test_invite_issuance_is_attributed_to_the_real_admin(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    cookies = _login_as_admin(client, monkeypatch)

    resp = client.post(
        "/admin/users/invite",
        json={"user_id": "nonadmin-1"},
        cookies=cookies,
        headers={"X-CSRF-Token": cookies["hac_csrf"]},
    )
    assert resp.status_code == 200

    from sqlmodel import select

    from app.control.audit import cache_session
    from app.models.cache import AuditLog

    with cache_session() as db:
        row = db.exec(
            select(AuditLog).where(AuditLog.event_type == "webchat.invite_created")
        ).first()
    assert row is not None
    assert row.actor_user_id == "admin-1"


def test_access_update_is_attributed_to_the_real_admin(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    cookies = _login_as_admin(client, monkeypatch)

    resp = client.patch(
        "/admin/users/nonadmin-1/access",
        json={"telegram_enabled": False},
        cookies=cookies,
        headers={"X-CSRF-Token": cookies["hac_csrf"]},
    )
    assert resp.status_code == 200

    from sqlmodel import select

    from app.control.audit import cache_session
    from app.models.cache import AuditLog

    with cache_session() as db:
        row = db.exec(
            select(AuditLog).where(AuditLog.event_type == "user.access_updated")
        ).first()
    assert row is not None
    assert row.actor_user_id == "admin-1"


def test_invite_issuance_via_break_glass_falls_back_to_admin_marker(
    client: TestClient,
) -> None:
    resp = client.post("/admin/users/invite", json={"user_id": "nonadmin-1"}, headers=_AUTH)
    assert resp.status_code == 200

    from sqlmodel import select

    from app.control.audit import cache_session
    from app.models.cache import AuditLog

    with cache_session() as db:
        row = db.exec(
            select(AuditLog).where(AuditLog.event_type == "webchat.invite_created")
        ).first()
    assert row is not None
    assert row.actor_user_id == "admin"
