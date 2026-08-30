"""Unit tests for app.webchat.app's router selection — exactly one of the
legacy picker/bearer router or the WebAuthn router is ever mounted, chosen
by settings.feature_webauthn_login. See
docs/household-identity-and-access-design.md's "Feature-flag-gated router
mounting" decision.

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

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.webchat.app import create_webchat_app


def test_legacy_router_mounted_when_flag_is_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "feature_webauthn_login", False)
    client = TestClient(create_webchat_app())

    assert client.get("/webauthn-common.js").status_code == 404
    assert client.post("/api/webauthn/login/options").status_code == 404


def test_webauthn_router_mounted_when_flag_is_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "feature_webauthn_login", True)
    client = TestClient(create_webchat_app())

    assert client.get("/webauthn-common.js").status_code == 200
    assert client.get("/api/users").status_code == 404
