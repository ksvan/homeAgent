from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI

from app.config import get_settings

logger = logging.getLogger(__name__)


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    settings = get_settings()

    from app.bot import handle_incoming_message
    from app.channels.telegram import TelegramChannel

    channel = TelegramChannel(
        token=settings.telegram_bot_token,
        on_message=handle_incoming_message,
    )
    await channel.initialize()
    app.state.telegram_channel = channel
    logger.info("Telegram webhook channel initialised")

    yield

    await channel.shutdown()
    logger.info("Telegram webhook channel shut down")


def create_app() -> FastAPI:
    app = FastAPI(
        title="HomeAgent",
        lifespan=_lifespan,
        docs_url=None,  # disable Swagger UI in production
        redoc_url=None,
    )

    from app.api.health import router as health_router
    from app.api.webhooks import router as webhook_router

    app.include_router(health_router)
    app.include_router(webhook_router)

    return app
