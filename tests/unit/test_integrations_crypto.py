"""Unit tests for app.integrations.crypto — Fernet key derivation from
app_secret_key (which is only guaranteed to be an arbitrary >=32-char
string, not a valid raw Fernet key)."""

from __future__ import annotations

import pytest

from app.integrations.crypto import DecryptionError, decrypt, encrypt


@pytest.fixture(autouse=True)
def _secret_key(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("APP_SECRET_KEY", "unit-test-secret-key-not-a-real-fernet-key")
    yield
    get_settings.cache_clear()


def test_round_trip() -> None:
    ciphertext = encrypt("super-secret-access-token")
    assert ciphertext != "super-secret-access-token"
    assert decrypt(ciphertext) == "super-secret-access-token"


def test_empty_string_round_trips_to_empty() -> None:
    assert encrypt("") == ""
    assert decrypt("") == ""


def test_arbitrary_length_secret_produces_valid_key() -> None:
    """app_secret_key is an arbitrary string, not a 32-byte urlsafe-base64
    Fernet key — encrypt/decrypt must work regardless of its raw length."""
    ciphertext = encrypt("token-value")
    assert decrypt(ciphertext) == "token-value"


def test_decrypt_fails_with_different_secret_key(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import get_settings

    ciphertext = encrypt("token-value")

    get_settings.cache_clear()
    monkeypatch.setenv("APP_SECRET_KEY", "a-completely-different-secret-key-value")
    get_settings.cache_clear()

    with pytest.raises(DecryptionError):
        decrypt(ciphertext)
