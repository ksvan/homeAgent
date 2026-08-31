"""
HomeAgent entry point.

Run via Docker Compose (production and local dev):
  docker compose up --build
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def _run_migrations() -> None:
    """Apply pending Alembic migrations on startup."""
    from alembic.config import Config

    from alembic import command

    ini_path = Path(__file__).resolve().parents[1] / "alembic.ini"
    cfg = Config(str(ini_path))
    command.upgrade(cfg, "heads")
    logger.info("Database migrations applied")


async def _run() -> None:
    """Start the webhook server (main port) and admin server (LAN-only port)."""
    import uvicorn
    from fastapi import FastAPI

    from app.api.server import create_app
    from app.config import get_settings
    from app.control.api import router as admin_router

    settings = get_settings()

    # Fail fast if APP_SECRET_KEY is missing or too weak in production.
    # Checked here (after migrations) rather than in Settings validation so that
    # the alembic migration step can still load Settings without a key configured.
    if settings.app_env == "production" and len(settings.app_secret_key) < 32:
        raise SystemExit(
            "ERROR: APP_SECRET_KEY must be a strong random string (≥32 chars) in production.\n"
            'Generate one with: python -c "import secrets; print(secrets.token_hex(32))"'
        )

    # Fail fast if web chat is enabled in production with WebAuthn still on
    # its localhost dev defaults — see docs/household-identity-and-access-
    # design.md Phase 5. Passkey ceremonies would silently fail (wrong RP
    # ID/origin) rather than a clear startup error, which is a worse
    # failure mode than refusing to start.
    if (
        settings.app_env == "production"
        and settings.feature_web_chat
        and (settings.webauthn_rp_id == "localhost" or "localhost" in settings.webauthn_origins)
    ):
        raise SystemExit(
            "ERROR: WEBAUTHN_RP_ID/WEBAUTHN_ORIGINS are still on localhost defaults "
            "with FEATURE_WEB_CHAT=true in production.\n"
            "Set WEBAUTHN_RP_ID to the real hostname and WEBAUTHN_ORIGINS to the "
            "exact origin(s) web chat and the admin dashboard are actually served from."
        )

    class _ChildServer(uvicorn.Server):
        """Uvicorn server that leaves asyncio's signal handlers untouched —
        used for the secondary apps (admin, web chat) that ride on the main
        app's lifespan/signal handling instead of owning their own."""

        def install_signal_handlers(self) -> None:
            pass

    # Main app — webhook + health only; owns the lifespan and OS signals
    main_app = create_app()
    main_server = uvicorn.Server(
        uvicorn.Config(
            main_app,
            host="0.0.0.0",
            port=settings.port,
            log_level=settings.log_level.lower(),
        )
    )

    # Admin app — separate LAN-only port, shares in-process state (event bus, scheduler…)
    admin_app = FastAPI(docs_url=None, redoc_url=None)
    admin_app.include_router(admin_router)
    admin_server = _ChildServer(
        uvicorn.Config(
            admin_app,
            host=settings.admin_host,
            port=settings.admin_port,
            log_level="warning",
        )
    )
    admin_task = asyncio.ensure_future(admin_server.serve())

    # Web chat app — own port, household-facing (see docs/web-chat-channel-design.md).
    # Also shares in-process state brought up by the main app's lifespan.
    webchat_server: uvicorn.Server | None = None
    webchat_task: "asyncio.Task[None] | None" = None
    if settings.feature_web_chat:
        from app.webchat.app import create_webchat_app

        webchat_app = create_webchat_app()
        webchat_server = _ChildServer(
            uvicorn.Config(
                webchat_app,
                host=settings.web_chat_host,
                port=settings.web_chat_port,
                log_level=settings.log_level.lower(),
            )
        )
        webchat_task = asyncio.ensure_future(webchat_server.serve())

    logger.info(
        "HomeAgent starting (webhook=%d admin=%d web_chat=%s)",
        settings.port,
        settings.admin_port,
        settings.web_chat_port if settings.feature_web_chat else "disabled",
    )

    await main_server.serve()  # blocks until SIGTERM; lifespan calls signal_stream_shutdown()

    # Main server exited — shut down admin/web-chat cleanly
    admin_server.should_exit = True
    try:
        await asyncio.wait_for(admin_task, timeout=5.0)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        admin_task.cancel()
        await asyncio.gather(admin_task, return_exceptions=True)

    if webchat_server is not None and webchat_task is not None:
        webchat_server.should_exit = True
        try:
            await asyncio.wait_for(webchat_task, timeout=5.0)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            webchat_task.cancel()
            await asyncio.gather(webchat_task, return_exceptions=True)


async def main() -> None:
    _run_migrations()
    await _run()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Shutting down")
