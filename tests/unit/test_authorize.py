"""Unit tests for app.policy.authorize — the single authoritative,
default-deny authorization decision point (see
docs/household-identity-and-access-design.md Option D).

Pure function, no DB — Principal is a plain dataclass, so these tests
don't need any fixtures.
"""

from __future__ import annotations

from app.policy.authorize import Principal, authorize


def _principal(**overrides: object) -> Principal:
    defaults: dict[str, object] = dict(user_id="user-1", is_active=True, is_admin=False)
    defaults.update(overrides)
    return Principal(**defaults)  # type: ignore[arg-type]


def test_no_principal_is_denied_on_every_surface() -> None:
    for surface in ("telegram", "web_chat", "admin"):
        result = authorize(None, surface)  # type: ignore[arg-type]
        assert result.allowed is False
        assert result.reason == "no_principal"


def test_inactive_account_is_denied_on_every_surface() -> None:
    principal = _principal(is_active=False)
    for surface in ("telegram", "web_chat", "admin"):
        result = authorize(principal, surface)  # type: ignore[arg-type]
        assert result.allowed is False
        assert result.reason == "account_disabled"


def test_inactive_takes_precedence_over_admin() -> None:
    """An inactive admin account is still denied — is_active is checked first."""
    principal = _principal(is_active=False, is_admin=True)
    result = authorize(principal, "admin")
    assert result.allowed is False
    assert result.reason == "account_disabled"


def test_non_admin_is_denied_admin_surface() -> None:
    principal = _principal(is_admin=False)
    result = authorize(principal, "admin")
    assert result.allowed is False
    assert result.reason == "not_admin"


def test_admin_is_allowed_admin_surface() -> None:
    principal = _principal(is_admin=True)
    result = authorize(principal, "admin")
    assert result.allowed is True
    assert result.reason == "ok"


def test_active_non_admin_is_allowed_telegram_and_web_chat() -> None:
    principal = _principal(is_admin=False)
    for surface in ("telegram", "web_chat"):
        result = authorize(principal, surface)  # type: ignore[arg-type]
        assert result.allowed is True
        assert result.reason == "ok"


def test_active_admin_is_allowed_every_surface() -> None:
    principal = _principal(is_admin=True)
    for surface in ("telegram", "web_chat", "admin"):
        result = authorize(principal, surface)  # type: ignore[arg-type]
        assert result.allowed is True
