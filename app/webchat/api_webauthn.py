"""
Web chat HTTP + WebSocket API — WebAuthn passkey login (Phase 1 of
docs/household-identity-and-access-design.md), the only web chat router.

The earlier anonymous picker/bearer-token router (app.webchat.api) was
removed at Phase 5, per the design doc's own release gate: "the old flow
still reachable alongside the new one" is equivalent to not having
shipped the new one at all, and the 2026-08-31 security re-review's BR-01
finding confirmed exactly this risk in the interim configuration-gated
state. This is now the only way to reach web chat, unconditionally
mounted by app.webchat.app.create_webchat_app.

Session identity travels in an HttpOnly/Secure/SameSite cookie, never in
localStorage or a URL (Goal 7 / Option D). CSRF: a synchronizer token set
alongside the session cookie, required on every state-changing request.
Every authenticated request and the WebSocket handshake go through the
single authorize() decision point (Option D) — not just "is this token
valid," but "is this account allowed on this surface right now."
"""

from __future__ import annotations

import json
import logging
import pathlib
import secrets
from typing import Any

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response, WebSocket
from fastapi.responses import HTMLResponse, PlainTextResponse
from pydantic import BaseModel

from app.config import get_settings
from app.control.audit import record_audit_event
from app.policy.authorize import authorize
from app.policy.principal import load_principal
from app.webchat.channel import get_web_channel
from app.webchat.client_ip import get_client_ip
from app.webchat.invites import get_valid_invite, mark_invite_used
from app.webchat.session import (
    SessionInfo,
    create_session,
    get_session,
    revoke_session,
    touch_session,
)
from app.webchat.static_files import serve_js
from app.webchat.webauthn import (
    WebAuthnError,
    build_login_options,
    build_registration_options,
    verify_login,
    verify_registration,
)
from app.webchat.ws_loop import run_chat_ws_loop

logger = logging.getLogger(__name__)

router = APIRouter(tags=["webchat-webauthn"])

_SESSION_COOKIE = "hac_session"  # "HomeAgent chat"
_CSRF_COOKIE = "hac_csrf"
_CSRF_HEADER = "X-CSRF-Token"
_COOKIE_MAX_AGE_SECONDS = 60 * 60 * 24 * 90  # matches web_chat_session_absolute_ttl_days default


def _cookie_secure() -> bool:
    """Secure=False only for local plain-HTTP development — see Option D's
    "environment-aware Secure behavior" requirement. Always true otherwise."""
    return not get_settings().is_development


def _set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        _SESSION_COOKIE,
        token,
        httponly=True,
        secure=_cookie_secure(),
        samesite="lax",
        max_age=_COOKIE_MAX_AGE_SECONDS,
        path="/",
    )


def _set_csrf_cookie(response: Response) -> str:
    csrf_token = secrets.token_urlsafe(32)
    # Deliberately NOT HttpOnly — the frontend JS must be able to read this
    # to echo it back in the X-CSRF-Token header. It's not a bearer
    # credential on its own (the session cookie is what actually
    # authenticates); it only proves the request came from a page that
    # could read this origin's cookies, which is exactly what defeats CSRF.
    response.set_cookie(
        _CSRF_COOKIE,
        csrf_token,
        httponly=False,
        secure=_cookie_secure(),
        samesite="lax",
        max_age=_COOKIE_MAX_AGE_SECONDS,
        path="/",
    )
    return csrf_token


async def _require_session(hac_session: str | None = Cookie(default=None)) -> SessionInfo:
    token = hac_session or ""
    session = get_session(token)
    if session is None:
        raise HTTPException(status_code=401, detail="Invalid or expired session")

    decision = authorize(load_principal(session.user_id), "web_chat")
    if not decision.allowed:
        raise HTTPException(status_code=403, detail=decision.reason)

    touch_session(token)
    return session


async def _require_csrf(request: Request, hac_csrf: str | None = Cookie(default=None)) -> None:
    header_value = request.headers.get(_CSRF_HEADER)
    if not hac_csrf or not header_value or not secrets.compare_digest(hac_csrf, header_value):
        raise HTTPException(status_code=403, detail="Missing or invalid CSRF token")


def _origin_allowed(origin: str | None) -> bool:
    if not origin:
        return False
    allowed = [o.strip() for o in get_settings().webauthn_origins.split(",") if o.strip()]
    return origin in allowed


async def _require_preauth_rate_limit(request: Request) -> None:
    """Pre-auth throttle for the invite-lookup and registration/login
    ceremony endpoints — there's no session yet to key a limit on
    User.id, so this is keyed by (trusted-proxy-aware) client IP instead.
    Reuses app.bot's existing sliding-window limiter rather than a
    second implementation."""
    from app.bot import _is_rate_limited

    ip = get_client_ip(
        request.client.host if request.client else None,
        request.headers.get("x-forwarded-for"),
    )
    if _is_rate_limited(
        f"webchat-preauth:{ip}", get_settings().webchat_preauth_rate_limit_per_minute
    ):
        raise HTTPException(status_code=429, detail="Too many attempts. Please wait a moment.")


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------


@router.get("/", response_class=HTMLResponse)
async def index() -> str:
    path = pathlib.Path(__file__).with_name("static") / "chat_webauthn.html"
    try:
        return path.read_text()
    except FileNotFoundError:
        logger.error("chat_webauthn.html missing — web chat UI unavailable")
        return "<html><body><pre>Web chat UI unavailable.</pre></body></html>"


@router.get("/webauthn-common.js")
async def webauthn_common_js() -> PlainTextResponse:
    return serve_js("webauthn-common.js")


@router.get("/chat_webauthn.js")
async def chat_webauthn_js() -> PlainTextResponse:
    return serve_js("chat_webauthn.js")


@router.get("/invite.js")
async def invite_js() -> PlainTextResponse:
    return serve_js("invite.js")


@router.get("/invite/{token}", response_class=HTMLResponse)
async def invite_page(token: str) -> str:
    path = pathlib.Path(__file__).with_name("static") / "invite.html"
    try:
        return path.read_text()
    except FileNotFoundError:
        logger.error("invite.html missing — invite claim page unavailable")
        return "<html><body><pre>Invite page unavailable.</pre></body></html>"


# ---------------------------------------------------------------------------
# Invite lookup (unauthenticated — the invite token itself is the secret)
# ---------------------------------------------------------------------------


@router.get("/api/invite/{token}", dependencies=[Depends(_require_preauth_rate_limit)])
async def get_invite(token: str) -> dict[str, Any]:
    invite = get_valid_invite(token)
    if invite is None:
        raise HTTPException(status_code=404, detail="Invalid or expired invite")

    from sqlmodel import select

    from app.db import users_session
    from app.models.users import User

    with users_session() as db:
        user = db.exec(select(User).where(User.id == invite.user_id)).first()
    if user is None:
        raise HTTPException(status_code=404, detail="Invalid invite")
    return {"user_id": user.id, "name": user.name}


# ---------------------------------------------------------------------------
# WebAuthn registration (invite-bound)
# ---------------------------------------------------------------------------


class RegistrationOptionsRequest(BaseModel):
    invite_token: str


@router.post("/api/webauthn/register/options", dependencies=[Depends(_require_preauth_rate_limit)])
async def webauthn_register_options(body: RegistrationOptionsRequest) -> dict[str, Any]:
    invite = get_valid_invite(body.invite_token)
    if invite is None:
        raise HTTPException(status_code=404, detail="Invalid or expired invite")

    from sqlmodel import select

    from app.db import users_session
    from app.models.users import User

    with users_session() as db:
        user = db.exec(select(User).where(User.id == invite.user_id)).first()
    if user is None:
        raise HTTPException(status_code=404, detail="Invalid invite")

    challenge_id, options_json = build_registration_options(user.id, user.name)
    return {"challenge_id": challenge_id, "options": json.loads(options_json)}


class RegistrationVerifyRequest(BaseModel):
    invite_token: str
    challenge_id: str
    credential: dict[str, Any]


@router.post("/api/webauthn/register/verify", dependencies=[Depends(_require_preauth_rate_limit)])
async def webauthn_register_verify(
    body: RegistrationVerifyRequest, response: Response
) -> dict[str, Any]:
    invite = get_valid_invite(body.invite_token)
    if invite is None:
        raise HTTPException(status_code=404, detail="Invalid or expired invite")

    # Verify BEFORE consuming the invite (2026-08-31 security re-review
    # finding BR-06): a failed/malformed ceremony must not burn a valid
    # invite the legitimate holder could otherwise retry with. The invite
    # is only actually claimed (mark_invite_used, a compare-and-swap) once
    # verification has already succeeded, right before the credential
    # itself is persisted — so a lost race between two concurrent claim
    # attempts is caught here, before either creates a credential, not
    # after.
    try:
        result = verify_registration(body.challenge_id, invite.user_id, json.dumps(body.credential))
    except WebAuthnError:
        logger.warning(
            "WebAuthn registration failed for invite user_id=%s", invite.user_id, exc_info=True
        )
        raise HTTPException(status_code=400, detail="Registration verification failed") from None

    if not mark_invite_used(body.invite_token):
        raise HTTPException(status_code=409, detail="Invite already claimed")

    from app.db import users_session as _users_session
    from app.models.users import WebAuthnCredential

    with _users_session() as db:
        db.add(
            WebAuthnCredential(
                user_id=result.user_id,
                credential_id=result.credential_id,
                public_key=result.public_key_b64,
                sign_count=result.sign_count,
            )
        )
        db.commit()

    record_audit_event(
        "webchat.credential_registered", invite.household_id, target_user_id=invite.user_id
    )

    session_info = create_session(invite.user_id, invite.household_id)
    _set_session_cookie(response, session_info.token)
    csrf_token = _set_csrf_cookie(response)
    return {"ok": True, "csrf_token": csrf_token}


# ---------------------------------------------------------------------------
# WebAuthn login (usernameless / discoverable credential)
# ---------------------------------------------------------------------------


@router.post("/api/webauthn/login/options", dependencies=[Depends(_require_preauth_rate_limit)])
async def webauthn_login_options() -> dict[str, Any]:
    challenge_id, options_json = build_login_options()
    return {"challenge_id": challenge_id, "options": json.loads(options_json)}


class LoginVerifyRequest(BaseModel):
    challenge_id: str
    credential: dict[str, Any]


@router.post("/api/webauthn/login/verify", dependencies=[Depends(_require_preauth_rate_limit)])
async def webauthn_login_verify(body: LoginVerifyRequest, response: Response) -> dict[str, Any]:
    try:
        result = verify_login(body.challenge_id, json.dumps(body.credential))
    except WebAuthnError:
        logger.warning("WebAuthn login failed", exc_info=True)
        raise HTTPException(status_code=401, detail="Login verification failed") from None

    from sqlmodel import select

    from app.db import users_session
    from app.models.users import User

    with users_session() as db:
        user = db.exec(select(User).where(User.id == result.user_id)).first()
    if user is None:
        raise HTTPException(status_code=401, detail="Login verification failed")

    decision = authorize(load_principal(user.id), "web_chat")
    if not decision.allowed:
        record_audit_event(
            "webchat.login_denied",
            user.household_id,
            actor_user_id=user.id,
            detail={"reason": decision.reason},
        )
        raise HTTPException(status_code=403, detail=decision.reason)

    record_audit_event("webchat.login_succeeded", user.household_id, actor_user_id=user.id)

    session_info = create_session(user.id, user.household_id)
    _set_session_cookie(response, session_info.token)
    csrf_token = _set_csrf_cookie(response)
    return {"ok": True, "name": user.name, "csrf_token": csrf_token}


# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------


@router.get("/api/me")
async def me(session: SessionInfo = Depends(_require_session)) -> dict[str, Any]:
    from sqlmodel import select

    from app.db import users_session
    from app.models.users import User

    with users_session() as db:
        user = db.exec(select(User).where(User.id == session.user_id)).first()
    return {"user_id": session.user_id, "name": user.name if user else ""}


@router.delete("/api/session")
async def end_session(
    response: Response,
    session: SessionInfo = Depends(_require_session),
    _csrf: None = Depends(_require_csrf),
) -> dict[str, bool]:
    revoke_session(session.token)
    response.delete_cookie(_SESSION_COOKIE, path="/")
    response.delete_cookie(_CSRF_COOKIE, path="/")
    record_audit_event("webchat.logout", session.household_id, actor_user_id=session.user_id)
    return {"ok": True}


# ---------------------------------------------------------------------------
# WebSocket
# ---------------------------------------------------------------------------


@router.websocket("/ws")
async def chat_ws(websocket: WebSocket) -> None:
    origin = websocket.headers.get("origin")
    if not _origin_allowed(origin):
        await websocket.close(code=4403)
        return

    token = websocket.cookies.get(_SESSION_COOKIE, "")
    session = get_session(token)
    if session is None:
        await websocket.close(code=4401)
        return

    decision = authorize(load_principal(session.user_id), "web_chat")
    if not decision.allowed:
        await websocket.close(code=4403)
        return

    channel = get_web_channel()
    max_connections = get_settings().webchat_max_connections_per_user
    if channel.connection_count_for_user(session.user_id) >= max_connections:
        await websocket.close(code=4429)
        return

    await websocket.accept()
    touch_session(token)
    channel.register_connection(session.token, websocket, user_id=session.user_id)

    await run_chat_ws_loop(websocket, session, channel, token, touch_session)
