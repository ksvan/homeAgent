"""
HomeAgent entry point.

Development:  APP_ENV=development uv run python -m app
Production:   APP_ENV=production  uv run python -m app  (runs FastAPI + webhook)
"""
from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Minimal logging setup before settings are loaded
# ---------------------------------------------------------------------------
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


async def _run_development() -> None:
    """Long-polling mode — no public URL required."""
    from app.agent.agent import reload_agent
    from app.bot import handle_incoming_message
    from app.channels.registry import set_channel
    from app.channels.telegram import TelegramChannel
    from app.config import get_settings
    from app.homey.mcp_client import start_mcp
    from app.logging_setup import configure_logging
    from app.prometheus.mcp_client import start_mcp as start_prom_mcp
    from app.policy.seeder import seed_policies
    from app.scheduler.cleanup import register_cleanup_jobs
    from app.scheduler.engine import start_scheduler
    from app.scheduler.actions import restore_pending_actions
    from app.scheduler.reminders import restore_pending_reminders

    settings = get_settings()
    configure_logging(settings.log_level, settings.log_format)

    if not settings.telegram_bot_token:
        logger.error("TELEGRAM_BOT_TOKEN is not set — cannot start bot")
        sys.exit(1)

    seed_policies()

    await start_mcp()
    await start_prom_mcp()
    reload_agent()  # pick up MCP toolset if connected

    await start_scheduler()
    await restore_pending_reminders()
    await restore_pending_actions()
    await register_cleanup_jobs()

    channel = TelegramChannel(
        token=settings.telegram_bot_token,
        on_message=handle_incoming_message,
    )
    set_channel(channel)

    # Start admin UI in the background (same process → shares the event bus)
    import uvicorn
    from fastapi import FastAPI
    from app.control.api import router as admin_router

    admin_app = FastAPI(docs_url=None, redoc_url=None)
    admin_app.include_router(admin_router)
    admin_server = uvicorn.Server(
        uvicorn.Config(admin_app, host="0.0.0.0", port=settings.port, log_level="warning")
    )
    asyncio.ensure_future(admin_server.serve())
    logger.info("Admin UI available at http://localhost:%d/admin", settings.port)

    logger.info("HomeAgent starting in development mode (Telegram polling)")
    await channel.start_polling()


async def _run_production() -> None:
    """Webhook mode via FastAPI + uvicorn."""
    import uvicorn

    from app.api.server import create_app
    from app.config import get_settings

    settings = get_settings()
    app = create_app()
    config = uvicorn.Config(
        app,
        host="0.0.0.0",
        port=settings.port,
        log_level=settings.log_level.lower(),
    )
    server = uvicorn.Server(config)
    logger.info("HomeAgent starting in production mode (port=%d)", settings.port)
    await server.serve()


async def main() -> None:
    _run_migrations()

    from app.config import get_settings

    settings = get_settings()
    if settings.is_development or settings.is_test:
        await _run_development()
    else:
        await _run_production()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Shutting down")
