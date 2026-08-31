"""Unit tests for app.policy.authorize — the single authoritative,
default-deny authorization decision point (see
docs/household-identity-and-access-design.md Option D).

Pure function, no DB — Principal is a plain dataclass, so these tests
don't need any fixtures.
"""

from __future__ import annotations

from app.policy.authorize import Principal, authorize


def _principal(**overrides: object) -> Principal:
    defaults: dict[str, object] = dict(
        user_id="user-1",
        is_active=True,
        is_admin=False,
        telegram_enabled=True,
        web_chat_enabled=True,
    )
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


def test_default_surface_flags_allow_telegram_and_web_chat() -> None:
    """Principal's telegram_enabled/web_chat_enabled default True — every
    existing account keeps today's access unless an admin turns one off."""
    principal = Principal(user_id="user-1", is_active=True, is_admin=False)
    assert authorize(principal, "telegram").allowed is True
    assert authorize(principal, "web_chat").allowed is True


def test_telegram_disabled_denies_only_telegram() -> None:
    principal = _principal(telegram_enabled=False)
    result = authorize(principal, "telegram")
    assert result.allowed is False
    assert result.reason == "telegram_disabled"
    assert authorize(principal, "web_chat").allowed is True


def test_web_chat_disabled_denies_only_web_chat() -> None:
    principal = _principal(web_chat_enabled=False)
    result = authorize(principal, "web_chat")
    assert result.allowed is False
    assert result.reason == "web_chat_disabled"
    assert authorize(principal, "telegram").allowed is True


def test_admin_surface_ignores_per_channel_flags() -> None:
    """An admin with both channel surfaces turned off is still admin —
    the admin surface only ever checks is_active and is_admin."""
    principal = _principal(is_admin=True, telegram_enabled=False, web_chat_enabled=False)
    result = authorize(principal, "admin")
    assert result.allowed is True


def test_account_disabled_takes_precedence_over_surface_flags() -> None:
    principal = _principal(is_active=False, telegram_enabled=True, web_chat_enabled=True)
    for surface in ("telegram", "web_chat"):
        result = authorize(principal, surface)  # type: ignore[arg-type]
        assert result.allowed is False
        assert result.reason == "account_disabled"
