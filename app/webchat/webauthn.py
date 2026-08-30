"""
WebAuthn ceremony helpers — registration (invite-bound) and login
(usernameless / discoverable-credential) — see
docs/household-identity-and-access-design.md Option F.

Discoverable credentials (resident keys) are the property that lets the
browser's own account chooser replace the app-rendered picker: the login
ceremony asks "who's there?" with no username step, and the assertion's
`userHandle`/`id` identifies which stored credential answered. Every
ceremony requires user verification (biometric/PIN on the authenticator
itself, not merely "a key was present").
"""

from __future__ import annotations

import base64
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import webauthn
from sqlmodel import select
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from app.config import get_settings
from app.db import cache_session, users_session
from app.models.cache import WebAuthnChallenge
from app.models.users import WebAuthnCredential

logger = logging.getLogger(__name__)


class WebAuthnError(Exception):
    """Any ceremony failure — callers translate this to an HTTP 400/401
    without leaking which specific check failed (Option G: generic
    pre-auth failure responses, no account enumeration)."""


def _b64_no_pad(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _b64_decode(value: str) -> bytes:
    padded = value + "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(padded)


def _expected_origins() -> list[str]:
    settings = get_settings()
    return [o.strip() for o in settings.webauthn_origins.split(",") if o.strip()]


def _store_challenge(challenge_bytes: bytes, purpose: str, user_id: str | None) -> str:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    with cache_session() as session:
        row = WebAuthnChallenge(
            purpose=purpose,
            challenge=_b64_no_pad(challenge_bytes),
            user_id=user_id,
            expires_at=now + timedelta(minutes=settings.webauthn_challenge_ttl_minutes),
        )
        session.add(row)
        session.commit()
        return row.id


def _consume_challenge(challenge_id: str, purpose: str) -> bytes | None:
    """One-shot: returns the raw challenge bytes if valid and unused, and
    marks it used in the same step so it can never be replayed."""
    now = datetime.now(timezone.utc)
    with cache_session() as session:
        row = session.exec(
            select(WebAuthnChallenge).where(WebAuthnChallenge.id == challenge_id)
        ).first()
        if row is None or row.purpose != purpose or row.used_at is not None:
            return None
        if row.expires_at.replace(tzinfo=timezone.utc) < now:
            return None
        row.used_at = now
        session.add(row)
        session.commit()
        return _b64_decode(row.challenge)


def build_registration_options(user_id: str, user_name: str) -> tuple[str, str]:
    """Returns (challenge_id, options_json) for a registration ceremony
    bound to user_id — the invite's target account (app.webchat.invites)."""
    settings = get_settings()
    options = webauthn.generate_registration_options(
        rp_id=settings.webauthn_rp_id,
        rp_name=settings.webauthn_rp_name,
        user_name=user_name,
        user_id=user_id.encode(),
        user_display_name=user_name,
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.REQUIRED,
            user_verification=UserVerificationRequirement.REQUIRED,
        ),
    )
    challenge_id = _store_challenge(options.challenge, "registration", user_id)
    return challenge_id, webauthn.options_to_json(options)


@dataclass
class RegisteredCredential:
    user_id: str
    credential_id: str
    public_key_b64: str
    sign_count: int


def verify_registration(
    challenge_id: str, user_id: str, credential_json: str
) -> RegisteredCredential:
    settings = get_settings()
    challenge = _consume_challenge(challenge_id, "registration")
    if challenge is None:
        raise WebAuthnError("expired_or_used_challenge")

    try:
        verified = webauthn.verify_registration_response(
            credential=credential_json,
            expected_challenge=challenge,
            expected_rp_id=settings.webauthn_rp_id,
            expected_origin=_expected_origins(),
            require_user_verification=True,
        )
    except Exception as exc:
        raise WebAuthnError(f"registration_verification_failed: {exc}") from exc

    return RegisteredCredential(
        user_id=user_id,
        credential_id=_b64_no_pad(verified.credential_id),
        public_key_b64=_b64_no_pad(verified.credential_public_key),
        sign_count=verified.sign_count,
    )


def build_login_options() -> tuple[str, str]:
    """Usernameless / discoverable-credential login: no allow_credentials,
    so the browser's own account chooser presents whatever passkeys it
    holds for this origin — see Option C's picker-removal decision."""
    settings = get_settings()
    options = webauthn.generate_authentication_options(
        rp_id=settings.webauthn_rp_id,
        user_verification=UserVerificationRequirement.REQUIRED,
    )
    challenge_id = _store_challenge(options.challenge, "login", None)
    return challenge_id, webauthn.options_to_json(options)


@dataclass
class AuthenticatedCredential:
    user_id: str
    credential_row_id: str
    new_sign_count: int


def verify_login(challenge_id: str, credential_json: str) -> AuthenticatedCredential:
    settings = get_settings()
    challenge = _consume_challenge(challenge_id, "login")
    if challenge is None:
        raise WebAuthnError("expired_or_used_challenge")

    try:
        payload = json.loads(credential_json)
        raw_credential_id = str(payload["id"])
    except Exception as exc:
        raise WebAuthnError("malformed_credential") from exc

    with users_session() as session:
        cred = session.exec(
            select(WebAuthnCredential).where(WebAuthnCredential.credential_id == raw_credential_id)
        ).first()
        if cred is None:
            raise WebAuthnError("unknown_credential")
        stored_user_id = cred.user_id
        stored_public_key = _b64_decode(cred.public_key)
        stored_sign_count = cred.sign_count
        cred_row_id = cred.id

    try:
        verified = webauthn.verify_authentication_response(
            credential=credential_json,
            expected_challenge=challenge,
            expected_rp_id=settings.webauthn_rp_id,
            expected_origin=_expected_origins(),
            credential_public_key=stored_public_key,
            credential_current_sign_count=stored_sign_count,
            require_user_verification=True,
        )
    except Exception as exc:
        raise WebAuthnError(f"login_verification_failed: {exc}") from exc

    # Cloned-authenticator check: many platform authenticators (Face ID/
    # Touch ID resident keys) never increment the counter at all and
    # legitimately report 0 forever — only treat a non-increase as
    # suspicious once either side has shown it actually counts.
    if not (stored_sign_count == 0 and verified.new_sign_count == 0):
        if verified.new_sign_count <= stored_sign_count:
            logger.warning(
                "WebAuthn sign_count did not increase (possible cloned authenticator): "
                "credential=%s stored=%d received=%d",
                raw_credential_id,
                stored_sign_count,
                verified.new_sign_count,
            )
            raise WebAuthnError("sign_count_regression")

    now = datetime.now(timezone.utc)
    with users_session() as session:
        cred = session.exec(
            select(WebAuthnCredential).where(WebAuthnCredential.id == cred_row_id)
        ).first()
        if cred is not None:
            cred.sign_count = verified.new_sign_count
            cred.last_used_at = now
            session.add(cred)
            session.commit()

    return AuthenticatedCredential(
        user_id=stored_user_id,
        credential_row_id=cred_row_id,
        new_sign_count=verified.new_sign_count,
    )
