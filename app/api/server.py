from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI

from app.config import get_settings

logger = logging.getLogger(__name__)


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    settings = get_settings()

    from sqlmodel import select

    from app.agent.agent import reload_agent
    from app.bot import handle_incoming_message
    from app.channels.registry import set_channel
    from app.channels.telegram import TelegramChannel
    from app.db import users_session
    from app.homey.mcp_client import start_mcp, stop_mcp
    from app.prometheus.mcp_client import start_mcp as start_prom_mcp
    from app.prometheus.mcp_client import stop_mcp as stop_prom_mcp
    from app.tools.mcp_client import start_mcp as start_tools_mcp
    from app.tools.mcp_client import stop_mcp as stop_tools_mcp
    from app.logging_setup import configure_logging
    from app.models.users import Household
    from app.policy.seeder import seed_policies
    from app.scheduler.actions import restore_pending_actions
    from app.scheduler.cleanup import register_cleanup_jobs
    from app.scheduler.engine import start_scheduler, stop_scheduler
    from app.scheduler.reminders import restore_pending_reminders
    from app.scheduler.scheduled_prompts import restore_scheduled_prompts

    configure_logging(settings.log_level, settings.log_format)
    seed_policies()

    # Start MCP servers so the agent can pick up their toolsets
    await start_mcp()
    await start_prom_mcp()
    await start_tools_mcp()
    reload_agent()  # rebuild agent singleton with all connected MCP toolsets

    # Start APScheduler, restore pending reminders, register cleanup job
    await start_scheduler()
    await restore_pending_reminders()
    await restore_pending_actions()
    await restore_scheduled_prompts()
    await register_cleanup_jobs()

    # Trigger home profile refresh in background (don't block startup)
    with users_session() as session:
        household = session.exec(select(Household)).first()

    if household:
        from app.homey.home_profile import refresh_home_profile

        asyncio.ensure_future(refresh_home_profile(household.id))

    channel = TelegramChannel(
        token=settings.telegram_bot_token,
        on_message=handle_incoming_message,
    )
    await channel.initialize()
    set_channel(channel)
    app.state.telegram_channel = channel
    logger.info("Telegram webhook channel initialised")

    yield

    # Signal admin SSE streams to close before uvicorn drains connections.
    from app.control.api import signal_stream_shutdown

    signal_stream_shutdown()

    await channel.shutdown()
    logger.info("Telegram webhook channel shut down")
    await stop_mcp()
    await stop_prom_mcp()
    await stop_tools_mcp()
    await stop_scheduler()


def create_app() -> FastAPI:
    app = FastAPI(
        title="HomeAgent",
        lifespan=_lifespan,
        docs_url=None,      # disable Swagger UI
        redoc_url=None,
        openapi_url=None,   # suppress schema discovery
    )

    from app.api.health import router as health_router
    from app.api.webhooks import router as webhook_router

    app.include_router(health_router)
    app.include_router(webhook_router)

    return app
