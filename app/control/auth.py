from __future__ import annotations

import secrets

from fastapi import HTTPException, Query, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

_bearer = HTTPBearer(auto_error=False)


async def require_admin_auth(
    credentials: HTTPAuthorizationCredentials | None = Security(_bearer),
    token: str | None = Query(default=None),
) -> None:
    """
    FastAPI dependency applied to all /admin routes.

    If APP_SECRET_KEY is not set (development), access is open — no change to
    current dev workflow. When set, requires either:
    - Authorization: Bearer <key>  header  (fetch / API clients — preferred)
    - ?token=<key>                 query param (EventSource only — browsers cannot
                                   send custom headers for SSE connections)

    The admin UI avoids the token appearing in the page address bar: on first
    load, JS strips ?token= from the URL via history.replaceState (so it never
    enters browser history or bookmarks), stores it in sessionStorage, and sends
    it as a Bearer header on all fetch() calls. The ?token= path exists only for
    the JS-constructed EventSource URL, which is not stored in browser history.
    """
    from app.config import get_settings

    key = get_settings().app_secret_key
    if not key:
        return  # dev mode — no key configured, open access

    candidate = (credentials.credentials if credentials else None) or token or ""
    if not secrets.compare_digest(candidate, key):
        raise HTTPException(status_code=401, detail="Unauthorized")
