"""Integration tests for app.webchat.api_webauthn — the WebAuthn-based
router mounted when settings.feature_webauthn_login is on. See
docs/household-identity-and-access-design.md Option D/F.

Exercises the full HTTP surface (invite lookup, registration, login,
/api/me, session teardown, WS handshake) against real in-memory users.db
and cache.db, with only the WebAuthn library's own cryptographic
verification mocked (covered separately, at the unit level, in
test_webchat_webauthn.py).
"""

from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel

import app.bot as bot_module
import app.control.audit as audit
import app.webchat.invites as invites
import app.webchat.session as wc_session
import app.webchat.webauthn as wa
from app.models.users import Household, User
from app.webchat.app import create_webchat_app


@pytest.fixture(autouse=True)
def clear_preauth_rate_limit() -> None:
    """The pre-auth rate limiter (Phase 3) reuses app.bot's process-wide
    sliding-window cache — reset it so one test's calls don't count
    against the next one's limit."""
    bot_module._user_call_times.clear()


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
    monkeypatch.setattr(invites, "cache_session", _cache_session)
    monkeypatch.setattr(wa, "cache_session", _cache_session)
    monkeypatch.setattr(wa, "users_session", _users_session)
    monkeypatch.setattr(audit, "cache_session", _cache_session)
    monkeypatch.setattr(
        "app.control.admin_events.emit_admin_event", lambda *a, **k: None, raising=False
    )

    return users_engine, cache_engine


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import get_settings

    s = get_settings()
    monkeypatch.setattr(s, "feature_webauthn_login", True)
    monkeypatch.setattr(s, "webauthn_origins", "http://testserver")


@pytest.fixture
def client(engines: tuple[object, object], settings: None) -> TestClient:
    users_engine, _ = engines
    with Session(users_engine) as s:  # type: ignore[arg-type]
        s.add(Household(id="hh-1", name="The Home"))
        s.add(User(id="user-1", household_id="hh-1", telegram_id=1001, name="Kristian"))
        s.add(
            User(
                id="user-2", household_id="hh-1", telegram_id=1002, name="Disabled", is_active=False
            )
        )
        s.commit()

    app = create_webchat_app()
    return TestClient(app)


def _create_invite(user_id: str = "user-1") -> str:
    info = invites.create_invite(user_id, "hh-1", created_by_user_id="admin")
    return info.token


def test_page_js_served_as_external_files(client: TestClient) -> None:
    """Phase 3: no inline <script> content in either page — both load
    their JS from these routes instead, which is what makes a
    script-src 'self' CSP with no 'unsafe-inline' possible."""
    for path in ("/webauthn-common.js", "/chat_webauthn.js", "/invite.js"):
        resp = client.get(path)
        assert resp.status_code == 200
        assert "javascript" in resp.headers["content-type"]


def test_get_invite_returns_target_user_name(client: TestClient) -> None:
    token = _create_invite()
    resp = client.get(f"/api/invite/{token}")
    assert resp.status_code == 200
    assert resp.json() == {"user_id": "user-1", "name": "Kristian"}


def test_get_invite_404_for_unknown_token(client: TestClient) -> None:
    resp = client.get("/api/invite/not-a-real-token")
    assert resp.status_code == 404


def test_register_options_requires_valid_invite(client: TestClient) -> None:
    resp = client.post("/api/webauthn/register/options", json={"invite_token": "bogus"})
    assert resp.status_code == 404


def test_register_options_returns_challenge(client: TestClient) -> None:
    token = _create_invite()
    resp = client.post("/api/webauthn/register/options", json={"invite_token": token})
    assert resp.status_code == 200
    body = resp.json()
    assert body["challenge_id"]
    assert body["options"]["user"]["name"] == "Kristian"


def test_register_verify_creates_credential_and_session(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = _create_invite()
    options_resp = client.post("/api/webauthn/register/options", json={"invite_token": token})
    challenge_id = options_resp.json()["challenge_id"]

    fake_result = SimpleNamespace(
        credential_id=b"cred-1", credential_public_key=b"pubkey", sign_count=0
    )
    monkeypatch.setattr(wa.webauthn, "verify_registration_response", lambda **kwargs: fake_result)

    resp = client.post(
        "/api/webauthn/register/verify",
        json={"invite_token": token, "challenge_id": challenge_id, "credential": {"id": "cred-1"}},
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    assert "hac_session" in resp.cookies
    assert "hac_csrf" in resp.cookies


def test_register_verify_rejects_reused_invite(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = _create_invite()
    options_resp = client.post("/api/webauthn/register/options", json={"invite_token": token})
    challenge_id = options_resp.json()["challenge_id"]

    fake_result = SimpleNamespace(
        credential_id=b"cred-1", credential_public_key=b"pubkey", sign_count=0
    )
    monkeypatch.setattr(wa.webauthn, "verify_registration_response", lambda **kwargs: fake_result)

    body = {"invite_token": token, "challenge_id": challenge_id, "credential": {"id": "cred-1"}}
    first = client.post("/api/webauthn/register/verify", json=body)
    assert first.status_code == 200

    second = client.post("/api/webauthn/register/verify", json=body)
    # get_valid_invite() already rejects a used invite (mark_invite_used's
    # own 409 branch only fires in the tight concurrent-claim race, covered
    # at the unit level in test_webchat_invites.py).
    assert second.status_code == 404


def _register_and_get_client_cookies(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, user_id: str = "user-1"
) -> dict[str, str]:
    token = _create_invite(user_id)
    options_resp = client.post("/api/webauthn/register/options", json={"invite_token": token})
    challenge_id = options_resp.json()["challenge_id"]
    raw_credential_id = f"cred-{user_id}".encode()
    fake_result = SimpleNamespace(
        credential_id=raw_credential_id, credential_public_key=b"pubkey", sign_count=0
    )
    monkeypatch.setattr(wa.webauthn, "verify_registration_response", lambda **kwargs: fake_result)
    resp = client.post(
        "/api/webauthn/register/verify",
        json={
            "invite_token": token,
            "challenge_id": challenge_id,
            "credential": {"id": f"cred-{user_id}"},
        },
    )
    assert resp.status_code == 200
    stored_credential_id = wa._b64_no_pad(raw_credential_id)
    return {
        "session": resp.cookies["hac_session"],
        "csrf": resp.cookies["hac_csrf"],
        "credential_id": stored_credential_id,
    }


def test_login_options_are_usernameless(client: TestClient) -> None:
    resp = client.post("/api/webauthn/login/options")
    assert resp.status_code == 200
    body = resp.json()
    assert body["challenge_id"]
    assert not body["options"].get("allowCredentials")


def test_login_verify_succeeds_for_registered_credential(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    reg = _register_and_get_client_cookies(client, monkeypatch)

    login_options = client.post("/api/webauthn/login/options")
    challenge_id = login_options.json()["challenge_id"]

    fake_result = SimpleNamespace(new_sign_count=0)
    monkeypatch.setattr(wa.webauthn, "verify_authentication_response", lambda **kwargs: fake_result)

    resp = client.post(
        "/api/webauthn/login/verify",
        json={"challenge_id": challenge_id, "credential": {"id": reg["credential_id"]}},
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "Kristian"
    assert "hac_session" in resp.cookies


def test_login_verify_denies_inactive_user(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    reg = _register_and_get_client_cookies(client, monkeypatch, user_id="user-2")

    login_options = client.post("/api/webauthn/login/options")
    challenge_id = login_options.json()["challenge_id"]
    fake_result = SimpleNamespace(new_sign_count=0)
    monkeypatch.setattr(wa.webauthn, "verify_authentication_response", lambda **kwargs: fake_result)

    resp = client.post(
        "/api/webauthn/login/verify",
        json={"challenge_id": challenge_id, "credential": {"id": reg["credential_id"]}},
    )
    assert resp.status_code == 403


def test_login_verify_rejects_unknown_credential(client: TestClient) -> None:
    login_options = client.post("/api/webauthn/login/options")
    challenge_id = login_options.json()["challenge_id"]

    resp = client.post(
        "/api/webauthn/login/verify",
        json={"challenge_id": challenge_id, "credential": {"id": "no-such-credential"}},
    )
    assert resp.status_code == 401


def test_me_requires_session_cookie(client: TestClient) -> None:
    resp = client.get("/api/me")
    assert resp.status_code == 401


def test_me_returns_identity_with_valid_session(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    cookies = _register_and_get_client_cookies(client, monkeypatch)
    resp = client.get("/api/me", cookies={"hac_session": cookies["session"]})
    assert resp.status_code == 200
    assert resp.json()["name"] == "Kristian"


def test_end_session_requires_csrf_header(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    cookies = _register_and_get_client_cookies(client, monkeypatch)
    resp = client.request(
        "DELETE",
        "/api/session",
        cookies={"hac_session": cookies["session"], "hac_csrf": cookies["csrf"]},
    )
    assert resp.status_code == 403


def test_end_session_succeeds_with_matching_csrf(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    cookies = _register_and_get_client_cookies(client, monkeypatch)
    resp = client.request(
        "DELETE",
        "/api/session",
        cookies={"hac_session": cookies["session"], "hac_csrf": cookies["csrf"]},
        headers={"X-CSRF-Token": cookies["csrf"]},
    )
    assert resp.status_code == 200

    follow_up = client.get("/api/me", cookies={"hac_session": cookies["session"]})
    assert follow_up.status_code == 401


def test_ws_rejects_disallowed_origin(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    cookies = _register_and_get_client_cookies(client, monkeypatch)
    with pytest.raises(Exception):
        with client.websocket_connect(
            "/ws",
            cookies={"hac_session": cookies["session"]},
            headers={"origin": "http://evil.example.com"},
        ):
            pass


def test_ws_rejects_missing_session(client: TestClient) -> None:
    with pytest.raises(Exception):
        with client.websocket_connect("/ws", headers={"origin": "http://testserver"}):
            pass


def test_ws_rejects_connection_beyond_per_user_cap(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The cap counts distinct sessions (WebChannel is keyed by session
    token), matching what "another device" actually means — reusing the
    same cookie across multiple tabs shares one token and so only ever
    counts once, exactly like a real browser sharing one cookie jar."""
    from contextlib import ExitStack

    from app.config import get_settings

    reg = _register_and_get_client_cookies(client, monkeypatch)

    monkeypatch.setattr(get_settings(), "webchat_max_connections_per_user", 2)
    fake_result = SimpleNamespace(new_sign_count=0)
    monkeypatch.setattr(wa.webauthn, "verify_authentication_response", lambda **kwargs: fake_result)

    session_tokens = [reg["session"]]
    for _ in range(2):
        login_options = client.post("/api/webauthn/login/options")
        challenge_id = login_options.json()["challenge_id"]
        resp = client.post(
            "/api/webauthn/login/verify",
            json={"challenge_id": challenge_id, "credential": {"id": reg["credential_id"]}},
        )
        assert resp.status_code == 200
        session_tokens.append(resp.cookies["hac_session"])

    with ExitStack() as stack:
        for token in session_tokens[:2]:
            stack.enter_context(
                client.websocket_connect(
                    "/ws",
                    cookies={"hac_session": token},
                    headers={"origin": "http://testserver"},
                )
            )
        with pytest.raises(Exception):
            with client.websocket_connect(
                "/ws",
                cookies={"hac_session": session_tokens[2]},
                headers={"origin": "http://testserver"},
            ):
                pass


# ---------------------------------------------------------------------------
# Phase 3 — pre-auth rate limiting
# ---------------------------------------------------------------------------


def test_preauth_rate_limit_returns_429_after_threshold(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "webchat_preauth_rate_limit_per_minute", 2)

    first = client.post("/api/webauthn/login/options")
    second = client.post("/api/webauthn/login/options")
    third = client.post("/api/webauthn/login/options")

    assert first.status_code == 200
    assert second.status_code == 200
    assert third.status_code == 429


def test_preauth_rate_limit_applies_to_invite_lookup_too(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "webchat_preauth_rate_limit_per_minute", 1)

    first = client.get("/api/invite/not-a-real-token")
    second = client.get("/api/invite/not-a-real-token")

    assert first.status_code == 404
    assert second.status_code == 429


def test_preauth_rate_limit_does_not_affect_authenticated_endpoints(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GET /api/me isn't a pre-auth endpoint — exhausting the pre-auth
    limit must not throttle it."""
    from app.config import get_settings

    # Register first, while the limit is still generous — registration
    # itself makes pre-auth calls that would otherwise exhaust a limit
    # this low before the cookies even exist.
    cookies = _register_and_get_client_cookies(client, monkeypatch)

    monkeypatch.setattr(get_settings(), "webchat_preauth_rate_limit_per_minute", 1)
    client.post("/api/webauthn/login/options")
    exhausted = client.post("/api/webauthn/login/options")
    assert exhausted.status_code == 429

    resp = client.get("/api/me", cookies={"hac_session": cookies["session"]})
    assert resp.status_code == 200
