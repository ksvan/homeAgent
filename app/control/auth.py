from __future__ import annotations

import secrets

from fastapi import HTTPException, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

_bearer = HTTPBearer(auto_error=False)


async def require_admin_auth(
    credentials: HTTPAuthorizationCredentials | None = Security(_bearer),
) -> None:
    """
    FastAPI dependency applied to all /admin routes.

    If APP_SECRET_KEY is not set (development), access is open — no change to
    current dev workflow. When set, requires 'Authorization: Bearer <key>'.
    """
    from app.config import get_settings

    key = get_settings().app_secret_key
    if not key:
        return  # dev mode — no key configured, open access

    if credentials is None or not secrets.compare_digest(credentials.credentials, key):
        raise HTTPException(status_code=401, detail="Unauthorized")
