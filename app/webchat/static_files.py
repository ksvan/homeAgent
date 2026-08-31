"""
Shared static-JS-serving helper for the web chat routers.

Both app.webchat.api (legacy) and app.webchat.api_webauthn serve their
page-specific JS as external files rather than inline <script> blocks —
required for a script-src 'self' CSP with no 'unsafe-inline'/nonce
(docs/household-identity-and-access-design.md Phase 3) — so this one
function backs every "GET /whatever.js" route in both routers instead of
each repeating the same read-and-404 logic.
"""

from __future__ import annotations

import logging
import pathlib

from fastapi.responses import PlainTextResponse

logger = logging.getLogger(__name__)

_STATIC_DIR = pathlib.Path(__file__).with_name("static")


def serve_js(filename: str) -> PlainTextResponse:
    path = _STATIC_DIR / filename
    try:
        return PlainTextResponse(path.read_text(), media_type="application/javascript")
    except FileNotFoundError:
        logger.error("%s missing next to app/webchat/static/ — page JS unavailable", filename)
        return PlainTextResponse("", media_type="application/javascript", status_code=404)
