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
    - Authorization: Bearer <key>  header  (curl / API clients)
    - ?token=<key>                 query param (plain browser navigation)
    """
    from app.config import get_settings

    key = get_settings().app_secret_key
    if not key:
        return  # dev mode — no key configured, open access

    # Accept bearer header OR ?token= query param
    candidate = (credentials.credentials if credentials else None) or token or ""
    if not secrets.compare_digest(candidate, key):
        raise HTTPException(status_code=401, detail="Unauthorized")
