"""OAuth 2.1 client for Oda's remote MCP server (https://oda.com/mcp).

Pure HTTP logic — no DB access here (see app.integrations.accounts for the
household-scoped account repository that calls into this module). Endpoints
are discovered at runtime from Oda's protected-resource and authorization-
server metadata documents (RFC 9728 / RFC 8414) rather than hardcoded, since
only the protected-resource metadata URL is a stable, documented constant.

See docs/oda-grocery-mcp-tool-design.md "Confirmed protocol details".
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx

PROTECTED_RESOURCE_METADATA_URL = "https://oda.com/.well-known/oauth-protected-resource/mcp"
MCP_URL = "https://oda.com/mcp"
SCOPE = "mcp"
CLIENT_NAME = "HomeAgent"

_HTTP_TIMEOUT = 15.0


class OdaOAuthError(Exception):
    """Raised for any failure talking to Oda's OAuth endpoints."""


@dataclass(frozen=True)
class OAuthServerMetadata:
    authorization_endpoint: str
    token_endpoint: str
    revocation_endpoint: str
    registration_endpoint: str


@dataclass(frozen=True)
class TokenResponse:
    access_token: str
    refresh_token: str
    expires_at: datetime


async def discover_metadata() -> OAuthServerMetadata:
    """Discover Oda's OAuth endpoints via the protected-resource metadata
    document, then the authorization server it points to.
    """
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
        try:
            resource_resp = await client.get(PROTECTED_RESOURCE_METADATA_URL)
            resource_resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise OdaOAuthError(f"Failed to fetch Oda protected-resource metadata: {exc}") from exc
        resource_meta = resource_resp.json()

        auth_servers = resource_meta.get("authorization_servers") or []
        if not auth_servers:
            raise OdaOAuthError("Oda protected-resource metadata has no authorization_servers")
        issuer = auth_servers[0]

        try:
            as_resp = await client.get(f"{issuer}/.well-known/oauth-authorization-server")
            as_resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise OdaOAuthError(
                f"Failed to fetch Oda authorization-server metadata: {exc}"
            ) from exc
        as_meta = as_resp.json()

    try:
        return OAuthServerMetadata(
            authorization_endpoint=as_meta["authorization_endpoint"],
            token_endpoint=as_meta["token_endpoint"],
            revocation_endpoint=as_meta.get("revocation_endpoint", ""),
            registration_endpoint=as_meta["registration_endpoint"],
        )
    except KeyError as exc:
        raise OdaOAuthError(f"Oda authorization-server metadata missing field: {exc}") from exc


def generate_pkce_pair() -> tuple[str, str]:
    """Return (code_verifier, code_challenge) for PKCE with S256."""
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(64)).rstrip(b"=").decode("ascii")
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest())
        .rstrip(b"=")
        .decode("ascii")
    )
    return verifier, challenge


def generate_state() -> str:
    return secrets.token_urlsafe(32)


async def register_client(metadata: OAuthServerMetadata, redirect_uri: str) -> tuple[str, str]:
    """Dynamic client registration (RFC 7591). Returns (client_id, client_secret) —
    client_secret is empty when the server issues a public (no-secret) client."""
    payload = {
        "client_name": CLIENT_NAME,
        "redirect_uris": [redirect_uri],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
    }
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
        try:
            resp = await client.post(metadata.registration_endpoint, json=payload)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise OdaOAuthError(f"Dynamic client registration failed: {exc}") from exc
        body = resp.json()

    try:
        client_id = body["client_id"]
    except KeyError as exc:
        raise OdaOAuthError("Dynamic client registration response missing client_id") from exc
    return client_id, body.get("client_secret", "")


def build_authorize_url(
    metadata: OAuthServerMetadata,
    *,
    client_id: str,
    redirect_uri: str,
    state: str,
    code_challenge: str,
) -> str:
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "scope": SCOPE,
    }
    return f"{metadata.authorization_endpoint}?{urlencode(params)}"


async def exchange_code(
    metadata: OAuthServerMetadata,
    *,
    client_id: str,
    client_secret: str,
    code: str,
    redirect_uri: str,
    code_verifier: str,
) -> TokenResponse:
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": client_id,
        "code_verifier": code_verifier,
    }
    if client_secret:
        data["client_secret"] = client_secret
    return await _post_token(metadata.token_endpoint, data)


async def refresh_access_token(
    metadata: OAuthServerMetadata,
    *,
    client_id: str,
    client_secret: str,
    refresh_token: str,
) -> TokenResponse:
    data = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id,
    }
    if client_secret:
        data["client_secret"] = client_secret
    return await _post_token(metadata.token_endpoint, data)


async def revoke_token(
    metadata: OAuthServerMetadata,
    *,
    client_id: str,
    client_secret: str,
    token: str,
) -> None:
    """Best-effort revocation (RFC 7009). No-op if the server didn't advertise
    a revocation_endpoint. Callers should treat a raised OdaOAuthError as
    non-fatal — a disconnect should still remove the local account even if
    Oda's revoke call fails."""
    if not metadata.revocation_endpoint or not token:
        return
    data = {"token": token, "client_id": client_id}
    if client_secret:
        data["client_secret"] = client_secret
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
        try:
            resp = await client.post(metadata.revocation_endpoint, data=data)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise OdaOAuthError(f"Oda token revocation failed: {exc}") from exc


async def _post_token(token_endpoint: str, data: dict[str, str]) -> TokenResponse:
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
        try:
            resp = await client.post(token_endpoint, data=data)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise OdaOAuthError(f"Oda token endpoint request failed: {exc}") from exc
        body = resp.json()

    try:
        access_token = body["access_token"]
    except KeyError as exc:
        raise OdaOAuthError("Oda token response missing access_token") from exc

    expires_in = int(body.get("expires_in", 3600))
    return TokenResponse(
        access_token=access_token,
        refresh_token=body.get("refresh_token", ""),
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=expires_in),
    )
