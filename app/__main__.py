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
    import signal as _signal

    from app.agent.agent import reload_agent
    from app.bot import handle_incoming_message
    from app.channels.registry import set_channel
    from app.channels.telegram import TelegramChannel
    from app.config import get_settings
    from app.homey.mcp_client import start_mcp
    from app.logging_setup import configure_logging
    from app.prometheus.mcp_client import start_mcp as start_prom_mcp
    from app.tools.mcp_client import start_mcp as start_tools_mcp
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
    await start_tools_mcp()
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

    class _AdminServer(uvicorn.Server):
        """Uvicorn server that leaves asyncio's signal handlers untouched."""

        def install_signal_handlers(self) -> None:
            pass

    admin_app = FastAPI(docs_url=None, redoc_url=None)
    admin_app.include_router(admin_router)
    admin_server = _AdminServer(
        uvicorn.Config(admin_app, host="0.0.0.0", port=settings.port, log_level="warning")
    )
    admin_task = asyncio.ensure_future(admin_server.serve())
    logger.info("Admin UI available at http://localhost:%d/admin", settings.port)

    # Install our own SIGINT/SIGTERM handler so we control shutdown order.
    # asyncio.run() installs a handler that cancels the main task, but that
    # causes cascading CancelledError through PTB's finally blocks before PTB
    # has a chance to clean up its internal polling HTTP task.
    loop = asyncio.get_running_loop()
    _stop = asyncio.Event()
    loop.add_signal_handler(_signal.SIGINT, _stop.set)
    loop.add_signal_handler(_signal.SIGTERM, _stop.set)

    logger.info("HomeAgent starting in development mode (Telegram polling)")

    # Run polling as a background task so we can also wait on the stop event.
    _polling = asyncio.ensure_future(channel.start_polling())

    # Wait until either polling exits on its own or a signal fires.
    _done, _pending = await asyncio.wait(
        {_polling, asyncio.ensure_future(_stop.wait())},
        return_when=asyncio.FIRST_COMPLETED,
    )
    for _t in _pending:
        _t.cancel()

    # Restore default signal behaviour before tearing down.
    loop.remove_signal_handler(_signal.SIGINT)
    loop.remove_signal_handler(_signal.SIGTERM)

    # Tell the polling task to stop (no-op if it already exited).
    _polling.cancel()

    # Signal SSE generators to close so uvicorn can drain connections cleanly.
    from app.control.api import signal_stream_shutdown

    signal_stream_shutdown()

    # Give SSE connections up to 1.5 s to exit before uvicorn starts shutting down.
    await asyncio.sleep(1.5)

    # Graceful uvicorn shutdown — sends proper lifespan.shutdown event to the ASGI
    # app rather than cancelling the task mid-flight (which causes noisy tracebacks).
    admin_server.should_exit = True
    try:
        await asyncio.wait_for(admin_task, timeout=5.0)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        admin_task.cancel()
        await asyncio.gather(admin_task, return_exceptions=True)

    # Cancel any remaining background tasks (PTB internals, scheduler tasks…).
    remaining = {t for t in asyncio.all_tasks() if t is not asyncio.current_task()}
    if remaining:
        for _t in remaining:
            _t.cancel()
        await asyncio.gather(*remaining, return_exceptions=True)


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
