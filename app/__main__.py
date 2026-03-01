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
    from app.bot import handle_incoming_message
    from app.channels.telegram import TelegramChannel
    from app.config import get_settings

    settings = get_settings()
    if not settings.telegram_bot_token:
        logger.error("TELEGRAM_BOT_TOKEN is not set — cannot start bot")
        sys.exit(1)

    channel = TelegramChannel(
        token=settings.telegram_bot_token,
        on_message=handle_incoming_message,
    )
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
