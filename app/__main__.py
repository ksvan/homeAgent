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
            "Generate one with: python -c \"import secrets; print(secrets.token_hex(32))\""
        )

    class _AdminServer(uvicorn.Server):
        """Uvicorn server that leaves asyncio's signal handlers untouched."""

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
    admin_server = _AdminServer(
        uvicorn.Config(admin_app, host="0.0.0.0", port=settings.admin_port, log_level="warning")
    )
    admin_task = asyncio.ensure_future(admin_server.serve())

    logger.info(
        "HomeAgent starting (webhook=%d admin=%d)",
        settings.port,
        settings.admin_port,
    )

    await main_server.serve()  # blocks until SIGTERM; lifespan calls signal_stream_shutdown()

    # Main server exited — shut down admin cleanly
    admin_server.should_exit = True
    try:
        await asyncio.wait_for(admin_task, timeout=5.0)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        admin_task.cancel()
        await asyncio.gather(admin_task, return_exceptions=True)


async def main() -> None:
    _run_migrations()
    await _run()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Shutting down")
