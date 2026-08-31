"""
Security response headers for the web chat app — see
docs/household-identity-and-access-design.md Phase 3.

Applied to every HTTP response from create_webchat_app() (both routers —
the CSP has to work for whichever one is currently mounted). Starlette's
BaseHTTPMiddleware only wraps HTTP requests, not the WebSocket handshake;
that's fine, these headers aren't meaningful on a WS upgrade response.

script-src is 'self' with no 'unsafe-inline'/nonce because every page
this app serves has zero inline <script> content — chat.html,
chat_webauthn.html, and invite.html all load their JS from external
files (app/webchat/static_files.py) specifically so this can stay this
simple. style-src keeps 'unsafe-inline' for each page's own <style>
block — CSS injection is a materially smaller blast radius than script
injection, and the design doc's CSP requirement is scoped to
script-src/object-src/base-uri, not style-src.

Permissions-Policy explicitly allows publickey-credentials-get/-create
for the top-level document — WebAuthn ceremonies fail if these are
denied, so a generic "lock everything down" policy would break passkey
login on this exact app.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

_CSP = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; "
    "connect-src 'self'; "
    "object-src 'none'; "
    "base-uri 'none'; "
    "frame-ancestors 'none'; "
    "form-action 'self'"
)

_PERMISSIONS_POLICY = (
    "publickey-credentials-get=(self), "
    "publickey-credentials-create=(self), "
    "camera=(), microphone=(), geolocation=()"
)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = _CSP
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = _PERMISSIONS_POLICY
        return response
