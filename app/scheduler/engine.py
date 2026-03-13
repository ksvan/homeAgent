from __future__ import annotations

import logging

from apscheduler import AsyncScheduler

logger = logging.getLogger(__name__)

_scheduler: AsyncScheduler | None = None


async def start_scheduler() -> None:
    """Start the APScheduler AsyncScheduler singleton."""
    global _scheduler
    if _scheduler is not None:
        logger.debug("Scheduler already running")
        return
    _scheduler = AsyncScheduler()
    await _scheduler.__aenter__()
    await _scheduler.start_in_background()
    logger.info("Scheduler started")


async def stop_scheduler() -> None:
    """Gracefully stop the scheduler singleton."""
    global _scheduler
    if _scheduler is None:
        return
    try:
        await _scheduler.__aexit__(None, None, None)
    except Exception:
        logger.debug("Scheduler stop error (ignored)", exc_info=True)
    _scheduler = None
    logger.info("Scheduler stopped")


def get_scheduler() -> AsyncScheduler | None:
    return _scheduler
