"""Unit tests for app.webchat.webauthn — challenge issuance/single-use and
the registration/login verification wrappers around the `webauthn`
library. See docs/household-identity-and-access-design.md Option F.

Ceremony cryptography itself (signature verification, attestation
parsing) is the `webauthn` library's own tested responsibility — these
tests mock `webauthn.verify_registration_response` /
`verify_authentication_response` and focus on this module's own logic:
challenge single-use/expiry, credential lookup, and the sign-count
regression (cloned authenticator) check.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlmodel import Session

import app.webchat.webauthn as wa
from app.models.cache import WebAuthnChallenge
from app.models.users import WebAuthnCredential


@pytest.fixture(autouse=True)
def patch_sessions(monkeypatch: pytest.MonkeyPatch, in_memory_engine: object) -> None:
    @contextmanager
    def _session():  # type: ignore[misc]
        with Session(in_memory_engine) as s:  # type: ignore[arg-type]
            yield s

    monkeypatch.setattr(wa, "cache_session", _session)
    monkeypatch.setattr(wa, "users_session", _session)


def _add_credential(user_id: str, credential_id: str, sign_count: int) -> None:
    with wa.users_session() as db:
        db.add(
            WebAuthnCredential(
                user_id=user_id,
                credential_id=credential_id,
                public_key=wa._b64_no_pad(b"public-key-bytes"),
                sign_count=sign_count,
            )
        )
        db.commit()


def test_build_registration_options_stores_challenge_bound_to_user() -> None:
    challenge_id, options_json = wa.build_registration_options("user-1", "Alice")
    options = json.loads(options_json)
    assert options["user"]["name"] == "Alice"

    with wa.cache_session() as db:
        row = db.get(WebAuthnChallenge, challenge_id)
        assert row is not None
        assert row.purpose == "registration"
        assert row.user_id == "user-1"
        assert row.used_at is None


def test_build_login_options_is_usernameless() -> None:
    challenge_id, options_json = wa.build_login_options()
    options = json.loads(options_json)
    assert not options.get("allowCredentials")

    with wa.cache_session() as db:
        row = db.get(WebAuthnChallenge, challenge_id)
        assert row is not None
        assert row.purpose == "login"
        assert row.user_id is None


def test_consume_challenge_is_single_use() -> None:
    challenge_id, _ = wa.build_registration_options("user-1", "Alice")
    assert wa._consume_challenge(challenge_id, "registration") is not None
    assert wa._consume_challenge(challenge_id, "registration") is None


def test_consume_challenge_rejects_wrong_purpose() -> None:
    challenge_id, _ = wa.build_registration_options("user-1", "Alice")
    assert wa._consume_challenge(challenge_id, "login") is None


def test_consume_challenge_rejects_expired() -> None:
    now = datetime.now(timezone.utc)
    with wa.cache_session() as db:
        row = WebAuthnChallenge(
            purpose="login",
            challenge=wa._b64_no_pad(b"chal"),
            expires_at=now - timedelta(minutes=1),
        )
        db.add(row)
        db.commit()
        challenge_id = row.id

    assert wa._consume_challenge(challenge_id, "login") is None


def test_verify_registration_rejects_expired_or_used_challenge() -> None:
    with pytest.raises(wa.WebAuthnError):
        wa.verify_registration("missing-challenge-id", "user-1", "{}")


def test_verify_registration_success(monkeypatch: pytest.MonkeyPatch) -> None:
    challenge_id, _ = wa.build_registration_options("user-1", "Alice")
    fake_result = SimpleNamespace(
        credential_id=b"cred-id-bytes", credential_public_key=b"pub-key-bytes", sign_count=0
    )
    monkeypatch.setattr(wa.webauthn, "verify_registration_response", lambda **kwargs: fake_result)

    result = wa.verify_registration(challenge_id, "user-1", "{}")
    assert result.user_id == "user-1"
    assert result.sign_count == 0
    assert result.credential_id == wa._b64_no_pad(b"cred-id-bytes")


def test_verify_registration_rejects_challenge_issued_for_a_different_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """2026-08-31 security re-review finding BR-07: a registration
    challenge issued for one invite's target account must not be usable
    to enroll a different account, even with an otherwise-valid
    (unexpired, unused, correct-purpose) challenge_id."""
    challenge_id, _ = wa.build_registration_options("user-1", "Alice")
    fake_result = SimpleNamespace(
        credential_id=b"cred-id-bytes", credential_public_key=b"pub-key-bytes", sign_count=0
    )
    monkeypatch.setattr(wa.webauthn, "verify_registration_response", lambda **kwargs: fake_result)

    with pytest.raises(wa.WebAuthnError):
        wa.verify_registration(challenge_id, "user-2", "{}")

    # A mismatched attempt does NOT consume the challenge — otherwise an
    # attacker could DoS the legitimate user-1 out of their own valid
    # enrollment just by replaying the challenge_id with a garbage
    # user_id. The real owner can still complete registration normally.
    result = wa.verify_registration(challenge_id, "user-1", "{}")
    assert result.user_id == "user-1"


def test_verify_registration_wraps_library_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    challenge_id, _ = wa.build_registration_options("user-1", "Alice")

    def _raise(**kwargs: object) -> None:
        raise ValueError("bad signature")

    monkeypatch.setattr(wa.webauthn, "verify_registration_response", _raise)

    with pytest.raises(wa.WebAuthnError):
        wa.verify_registration(challenge_id, "user-1", "{}")


def test_verify_login_rejects_unknown_credential(monkeypatch: pytest.MonkeyPatch) -> None:
    challenge_id, _ = wa.build_login_options()
    credential_json = json.dumps({"id": "unknown-credential-id"})

    with pytest.raises(wa.WebAuthnError):
        wa.verify_login(challenge_id, credential_json)


def test_verify_login_success_advances_sign_count(monkeypatch: pytest.MonkeyPatch) -> None:
    _add_credential("user-1", "cred-abc", sign_count=5)
    challenge_id, _ = wa.build_login_options()
    credential_json = json.dumps({"id": "cred-abc"})

    fake_result = SimpleNamespace(new_sign_count=6)
    monkeypatch.setattr(wa.webauthn, "verify_authentication_response", lambda **kwargs: fake_result)

    result = wa.verify_login(challenge_id, credential_json)
    assert result.user_id == "user-1"
    assert result.new_sign_count == 6

    with wa.users_session() as db:
        from sqlmodel import select

        cred = db.exec(
            select(WebAuthnCredential).where(WebAuthnCredential.credential_id == "cred-abc")
        ).first()
        assert cred is not None
        assert cred.sign_count == 6


def test_verify_login_zero_sign_count_is_not_a_regression(monkeypatch: pytest.MonkeyPatch) -> None:
    """Many platform authenticators (Face ID / Touch ID resident keys)
    never increment the counter and legitimately report 0 forever."""
    _add_credential("user-1", "cred-abc", sign_count=0)
    challenge_id, _ = wa.build_login_options()
    credential_json = json.dumps({"id": "cred-abc"})

    fake_result = SimpleNamespace(new_sign_count=0)
    monkeypatch.setattr(wa.webauthn, "verify_authentication_response", lambda **kwargs: fake_result)

    result = wa.verify_login(challenge_id, credential_json)
    assert result.new_sign_count == 0


def test_verify_login_rejects_sign_count_regression(monkeypatch: pytest.MonkeyPatch) -> None:
    _add_credential("user-1", "cred-abc", sign_count=5)
    challenge_id, _ = wa.build_login_options()
    credential_json = json.dumps({"id": "cred-abc"})

    fake_result = SimpleNamespace(new_sign_count=5)
    monkeypatch.setattr(wa.webauthn, "verify_authentication_response", lambda **kwargs: fake_result)

    with pytest.raises(wa.WebAuthnError):
        wa.verify_login(challenge_id, credential_json)


def test_verify_login_rejects_used_challenge(monkeypatch: pytest.MonkeyPatch) -> None:
    _add_credential("user-1", "cred-abc", sign_count=0)
    challenge_id, _ = wa.build_login_options()
    credential_json = json.dumps({"id": "cred-abc"})
    fake_result = SimpleNamespace(new_sign_count=0)
    monkeypatch.setattr(wa.webauthn, "verify_authentication_response", lambda **kwargs: fake_result)

    wa.verify_login(challenge_id, credential_json)
    with pytest.raises(wa.WebAuthnError):
        wa.verify_login(challenge_id, credential_json)
