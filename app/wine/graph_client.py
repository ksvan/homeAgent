from __future__ import annotations

import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_GRAPH_BASE = "https://graph.microsoft.com/v1.0"
_TOKEN_URL = "https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"

# Simple in-process token cache
_token_cache: dict[str, object] = {}


async def _get_token(tenant_id: str, client_id: str, client_secret: str) -> str:
    """Obtain a client-credentials access token, cached until expiry."""
    import httpx

    cache_key = f"{tenant_id}:{client_id}"
    cached = _token_cache.get(cache_key)
    if cached:
        expires_at = cached["expires_at"]  # type: ignore[index]
        if datetime.now(timezone.utc).timestamp() < expires_at - 60:
            return str(cached["token"])  # type: ignore[index]

    url = _TOKEN_URL.format(tenant_id=tenant_id)
    data = {
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret,
        "scope": "https://graph.microsoft.com/.default",
    }
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(url, data=data)
        resp.raise_for_status()
        body = resp.json()

    token = body["access_token"]
    expires_in = int(body.get("expires_in", 3600))
    _token_cache[cache_key] = {
        "token": token,
        "expires_at": datetime.now(timezone.utc).timestamp() + expires_in,
    }
    return str(token)


async def get_item_etag(
    tenant_id: str,
    client_id: str,
    client_secret: str,
    drive_id: str,
    item_id: str,
) -> str | None:
    """Fetch the driveItem metadata and return its eTag (lightweight check)."""
    import httpx

    token = await _get_token(tenant_id, client_id, client_secret)
    url = f"{_GRAPH_BASE}/drives/{drive_id}/items/{item_id}"
    headers = {"Authorization": f"Bearer {token}"}

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(url, headers=headers)
        if resp.status_code == 404:
            logger.warning("Wine workbook item not found: drive=%s item=%s", drive_id, item_id)
            return None
        resp.raise_for_status()
        body = resp.json()

    return body.get("eTag") or body.get("cTag") or ""


async def download_workbook(
    tenant_id: str,
    client_id: str,
    client_secret: str,
    drive_id: str,
    item_id: str,
) -> tuple[bytes, str]:
    """
    Download the xlsx workbook content.

    Returns (content_bytes, etag). Follows the Graph redirect to the download URL.
    """
    import httpx

    token = await _get_token(tenant_id, client_id, client_secret)
    url = f"{_GRAPH_BASE}/drives/{drive_id}/items/{item_id}/content"
    headers = {"Authorization": f"Bearer {token}"}

    async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
        resp = await client.get(url, headers=headers)
        resp.raise_for_status()
        content = resp.content

    if len(content) > 10 * 1024 * 1024:
        raise ValueError(f"Workbook download too large: {len(content)} bytes (limit 10 MB)")

    # Get a fresh eTag now that we've downloaded (the content request may not return it)
    etag = await get_item_etag(tenant_id, client_id, client_secret, drive_id, item_id) or ""
    return content, etag
