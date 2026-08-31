"""Unit tests for app.webchat.security_headers — the CSP and other
response headers applied to every web chat HTTP response. See
docs/household-identity-and-access-design.md Phase 3.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.webchat.app import create_webchat_app


@pytest.fixture
def legacy_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(get_settings(), "feature_webauthn_login", False)
    return TestClient(create_webchat_app())


@pytest.fixture
def webauthn_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(get_settings(), "feature_webauthn_login", True)
    return TestClient(create_webchat_app())


def test_csp_has_no_unsafe_inline_or_eval_for_scripts(legacy_client: TestClient) -> None:
    resp = legacy_client.get("/")
    csp = resp.headers["content-security-policy"]
    assert "script-src 'self'" in csp
    assert "unsafe-inline" not in csp.split("script-src")[1].split(";")[0]
    assert "unsafe-eval" not in csp


def test_csp_locks_down_object_and_base_uri(legacy_client: TestClient) -> None:
    csp = legacy_client.get("/").headers["content-security-policy"]
    assert "object-src 'none'" in csp
    assert "base-uri 'none'" in csp
    assert "frame-ancestors 'none'" in csp


def test_permissions_policy_allows_webauthn(webauthn_client: TestClient) -> None:
    policy = webauthn_client.get("/").headers["permissions-policy"]
    assert "publickey-credentials-get=(self)" in policy
    assert "publickey-credentials-create=(self)" in policy


def test_other_security_headers_present(legacy_client: TestClient) -> None:
    resp = legacy_client.get("/")
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.headers["x-frame-options"] == "DENY"
    assert resp.headers["referrer-policy"] == "no-referrer"


def test_headers_present_on_webauthn_router_too(webauthn_client: TestClient) -> None:
    resp = webauthn_client.get("/")
    assert "content-security-policy" in resp.headers
    assert resp.headers["x-content-type-options"] == "nosniff"


def test_headers_present_on_non_html_responses_too(legacy_client: TestClient) -> None:
    # A plain static-file route (no DB access) — this test intentionally
    # doesn't wire up an in-memory DB, so it must avoid any route that
    # would otherwise fall through to the real on-disk database.
    resp = legacy_client.get("/chat.js")
    assert "content-security-policy" in resp.headers
