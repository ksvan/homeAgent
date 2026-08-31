from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import Literal

from fastapi import Cookie, HTTPException, Query, Request, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

_bearer = HTTPBearer(auto_error=False)

SESSION_COOKIE = "hac_session"
CSRF_COOKIE = "hac_csrf"
CSRF_HEADER = "X-CSRF-Token"


@dataclass(frozen=True)
class AdminIdentity:
    """Who/what authenticated this admin request — see
    docs/household-identity-and-access-design.md Phase 4.

    `via="open"` only ever happens with no APP_SECRET_KEY configured
    (dev mode, unchanged pre-Phase-4 behavior). `via="break_glass"` is
    the shared-secret path, kept deliberately (not removed) as the
    local/LAN-only audited recovery path the design doc requires;
    `via="passkey"` is the normal per-person path this phase adds.
    """

    user_id: str | None
    via: Literal["passkey", "break_glass", "open"]


async def require_admin_auth(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Security(_bearer),
    token: str | None = Query(default=None),
    hac_session: str | None = Cookie(default=None),
    hac_csrf: str | None = Cookie(default=None),
) -> AdminIdentity:
    """
    FastAPI dependency applied to all /admin routes.

    If APP_SECRET_KEY is not set (development), access is open — no change to
    current dev workflow. When set, requires one of:
    - Authorization: Bearer <key>  header, or ?token=<key>  — the original
      shared-secret path, kept as an always-available break-glass recovery
      route (docs/household-identity-and-access-design.md Scope: "recovery
      must go only through the local/LAN-only audited break-glass path").
      A successful break-glass auth on a state-changing (non-GET) request
      is durably audited.
    - A valid admin passkey session cookie (same WebAuthn plumbing as web
      chat login — app.webchat.webauthn/app.webchat.session — gated by
      authorize(principal, "admin") rather than "web_chat"). State-
      changing requests additionally require a matching X-CSRF-Token
      header, exactly like web chat's cookie auth — a Bearer/break-glass
      request can't be forged cross-site the way a cookie-carrying one
      can, so CSRF is only enforced on the cookie path.

    Both paths remain valid on every /admin route for as long as
    APP_SECRET_KEY stays configured; this is deliberate, not a transition
    step to be "finished" by removing one — see Phase 4 above.
    """
    from app.config import get_settings

    settings = get_settings()
    key = settings.app_secret_key
    if not key:
        return AdminIdentity(user_id=None, via="open")

    candidate = (credentials.credentials if credentials else None) or token or ""
    if secrets.compare_digest(candidate, key):
        if request.method != "GET":
            from app.control.audit import record_audit_event

            record_audit_event(
                "admin.break_glass_used",
                "",
                detail={"method": request.method, "path": request.url.path},
            )
        return AdminIdentity(user_id=None, via="break_glass")

    if hac_session:
        from app.policy.authorize import authorize
        from app.policy.principal import load_principal
        from app.webchat.session import get_session, touch_session

        session = get_session(hac_session)
        if session is not None:
            decision = authorize(load_principal(session.user_id), "admin")
            if decision.allowed:
                if request.method != "GET":
                    header_value = request.headers.get(CSRF_HEADER)
                    if (
                        not hac_csrf
                        or not header_value
                        or not secrets.compare_digest(hac_csrf, header_value)
                    ):
                        raise HTTPException(status_code=403, detail="Missing or invalid CSRF token")
                touch_session(hac_session)
                return AdminIdentity(user_id=session.user_id, via="passkey")

    raise HTTPException(status_code=401, detail="Unauthorized")
