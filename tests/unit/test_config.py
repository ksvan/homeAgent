"""Unit tests for app.config — validators and helpers."""
from __future__ import annotations

import pytest

from app.config import Settings


def _settings(**overrides: object) -> Settings:
    """Create a Settings instance with sensible test defaults."""
    defaults = {
        "telegram_bot_token": "",
        "telegram_webhook_secret": "",
        "app_env": "test",
    }
    defaults.update(overrides)
    return Settings(**defaults)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# parse_int_list validator
# ---------------------------------------------------------------------------


def test_parse_int_list_comma_string() -> None:
    s = _settings(allowed_telegram_ids="1,2,3")
    assert s.allowed_telegram_ids == [1, 2, 3]


def test_parse_int_list_single_int() -> None:
    s = _settings(allowed_telegram_ids=42)
    assert s.allowed_telegram_ids == [42]


def test_parse_int_list_empty_string() -> None:
    s = _settings(allowed_telegram_ids="")
    assert s.allowed_telegram_ids == []


def test_parse_int_list_with_spaces() -> None:
    s = _settings(allowed_telegram_ids=" 10 , 20 ")
    assert s.allowed_telegram_ids == [10, 20]


def test_parse_int_list_real_list() -> None:
    s = _settings(allowed_telegram_ids=[5, 6])
    assert s.allowed_telegram_ids == [5, 6]


# ---------------------------------------------------------------------------
# _require_webhook_secret validator
# ---------------------------------------------------------------------------


def test_webhook_secret_required_when_bot_token_set() -> None:
    with pytest.raises(ValueError, match="TELEGRAM_WEBHOOK_SECRET"):
        _settings(telegram_bot_token="tok:123", telegram_webhook_secret="")


def test_webhook_secret_ok_when_both_set() -> None:
    s = _settings(telegram_bot_token="tok:123", telegram_webhook_secret="abc")
    assert s.telegram_bot_token == "tok:123"


def test_webhook_secret_not_required_without_bot_token() -> None:
    s = _settings(telegram_bot_token="", telegram_webhook_secret="")
    assert s.telegram_webhook_secret == ""


# ---------------------------------------------------------------------------
# Property helpers
# ---------------------------------------------------------------------------


def test_is_development() -> None:
    assert _settings(app_env="development").is_development is True
    assert _settings(app_env="production").is_development is False


def test_is_production() -> None:
    assert _settings(app_env="production").is_production is True


def test_is_test() -> None:
    assert _settings(app_env="test").is_test is True


def test_db_path() -> None:
    s = _settings(db_dir="/tmp/db")
    assert s.db_path("users") == "/tmp/db/users.db"


def test_features_property() -> None:
    s = _settings(feature_policy_gate=False)
    assert s.features.policy_gate is False
    assert s.features.cheap_background_models is True
