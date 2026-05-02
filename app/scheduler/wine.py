from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def wine_refresh_job() -> None:
    """APScheduler CronTrigger job — daily background wine cellar sync."""
    from app.control.events import emit
    from app.wine.sync import sync_wine_cellar

    logger.info("Wine daily refresh starting")
    result = await sync_wine_cellar(force=False)
    if result.success:
        emit(
            "job.complete",
            {"job": "wine_refresh", "row_count": result.row_count, "stale": result.stale},
        )
        logger.info("Wine daily refresh complete: %d bottles", result.row_count)
    else:
        emit("job.error", {"job": "wine_refresh", "error": result.error})
        logger.warning("Wine daily refresh failed: %s", result.error)


async def schedule_wine_refresh() -> None:
    """Register the daily wine refresh CronTrigger job with APScheduler."""
    from zoneinfo import ZoneInfo

    from app.config import get_settings
    from app.scheduler.engine import get_scheduler

    settings = get_settings()
    scheduler = get_scheduler()
    if scheduler is None:
        logger.warning("Scheduler not running — wine refresh job not registered")
        return

    try:
        from apscheduler.triggers.cron import CronTrigger

        parts = settings.wine_refresh_cron.split()
        if len(parts) != 5:
            logger.warning(
                "Invalid WINE_REFRESH_CRON %r (expected 5 parts) — using default 06:00",
                settings.wine_refresh_cron,
            )
            parts = ["0", "6", "*", "*", "*"]

        trigger = CronTrigger(
            minute=parts[0],
            hour=parts[1],
            day=parts[2],
            month=parts[3],
            day_of_week=parts[4],
            timezone=ZoneInfo(settings.household_timezone),
        )
        await scheduler.add_schedule(wine_refresh_job, trigger, id="wine_daily_refresh")
        logger.info("Wine daily refresh job registered (cron=%s)", settings.wine_refresh_cron)
    except Exception:
        logger.warning("Failed to register wine refresh job", exc_info=True)
