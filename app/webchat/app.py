"""
Standalone FastAPI app for the web chat channel.

Own process (in the homeagent container), own port — not mounted on the
main app (webhooks, port 8080) or the admin app (port 9090). See "Serving
pattern" in docs/web-chat-channel-design.md. No lifespan of its own: it
shares in-process state (DB engines, agent singleton, MCP toolsets, channel
registry) already brought up by the main app's lifespan, the same way the
existing admin app does.
"""

from __future__ import annotations

from fastapi import FastAPI

from app.config import get_settings
from app.webchat.security_headers import SecurityHeadersMiddleware


def create_webchat_app() -> FastAPI:
    app = FastAPI(
        title="HomeAgent Web Chat",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.add_middleware(SecurityHeadersMiddleware)

    if get_settings().feature_webauthn_login:
        from app.webchat.api_webauthn import router
    else:
        from app.webchat.api import router

    app.include_router(router)
    return app
