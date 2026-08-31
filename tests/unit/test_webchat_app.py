"""Unit tests for app.webchat.app — the WebAuthn router is the only web
chat router, unconditionally mounted. See
docs/household-identity-and-access-design.md Phase 5: the earlier
anonymous picker/bearer-token router was removed (not just gated behind
a flag) per the design doc's own release gate and the 2026-08-31 security
re-review's BR-01 finding.

Exercised via real HTTP requests (TestClient) rather than by inspecting
FastAPI's internal route objects, whose shape isn't a stable public API
across FastAPI versions.

Only routes that 404 before any handler runs, or that serve a static file
with no DB access, are checked here — deliberately avoiding any route that
would touch cache.db/users.db, since this test doesn't wire up isolated
in-memory engines (see the DB_DIR / APP_ENV=test gotcha in project memory:
an unwired call would fall through to the real on-disk database).
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.webchat.app import create_webchat_app


def test_webauthn_router_is_mounted() -> None:
    client = TestClient(create_webchat_app())

    # Static-file routes only — no DB access, unlike /api/webauthn/*
    # (which would otherwise fall through to the real on-disk database;
    # see the module docstring).
    assert client.get("/webauthn-common.js").status_code == 200
    assert client.get("/chat_webauthn.js").status_code == 200
    assert client.get("/invite.js").status_code == 200


def test_legacy_anonymous_endpoints_do_not_exist() -> None:
    """GET /api/users and POST /api/session were the legacy router's
    unauthenticated impersonation surface (BR-01) — confirm they're gone
    outright, not merely unreachable behind a flag. (POST /api/session
    is 405, not 404: the WebAuthn router has DELETE /api/session at the
    same path — the important thing is it's not a 200 that creates an
    anonymous session for an arbitrary supplied user_id.)"""
    client = TestClient(create_webchat_app())

    assert client.get("/api/users").status_code == 404
    assert client.post("/api/session", json={"user_id": "anyone"}).status_code == 405
