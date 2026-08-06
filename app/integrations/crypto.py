"""Symmetric encryption for OAuth tokens stored in IntegrationAccount rows.

`settings.app_secret_key` is only guaranteed to be an arbitrary >=32-char
string (see app/__main__.py's production startup check) — not a valid
Fernet key, which must be exactly 32 url-safe base64-encoded bytes. Derive a
stable Fernet key from it instead of passing it to Fernet() directly. See
docs/oda-grocery-mcp-tool-design.md "Encryption".
"""

from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from app.config import get_settings

__all__ = ["encrypt", "decrypt", "DecryptionError"]

# Fixed context string — keeps this derived key distinct from any other
# key that might one day be derived from the same app_secret_key.
_KEY_PURPOSE = "oda-integration-tokens-v1"


class DecryptionError(Exception):
    """Raised when a stored value can't be decrypted with the current
    app_secret_key — most likely because the key was rotated."""


def _derive_fernet_key(secret: str) -> bytes:
    digest = hashlib.sha256(f"{_KEY_PURPOSE}:{secret}".encode()).digest()
    return base64.urlsafe_b64encode(digest)


def _fernet() -> Fernet:
    return Fernet(_derive_fernet_key(get_settings().app_secret_key))


def encrypt(plaintext: str) -> str:
    if not plaintext:
        return ""
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str) -> str:
    if not ciphertext:
        return ""
    try:
        return _fernet().decrypt(ciphertext.encode()).decode()
    except InvalidToken as exc:
        raise DecryptionError(
            "Could not decrypt stored integration token — app_secret_key may have "
            "changed since it was stored."
        ) from exc
