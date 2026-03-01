from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

_CLEANUP_JOB_ID = "cleanup_old_logs"


async def purge_old_logs() -> None:
    """
    Delete EventLog and AgentRunLog rows older than the configured retention windows.
    Runs daily via APScheduler.
    """
    from sqlmodel import col, delete

    from app.config import get_settings
    from app.db import cache_session
    from app.models.cache import AgentRunLog, EventLog

    settings = get_settings()
    now = datetime.now(timezone.utc)
    event_cutoff = now - timedelta(days=settings.event_log_retention_days)
    run_cutoff = now - timedelta(days=settings.run_log_retention_days)

    with cache_session() as session:
        event_result = session.exec(
            delete(EventLog).where(col(EventLog.created_at) < event_cutoff)
        )
        run_result = session.exec(
            delete(AgentRunLog).where(col(AgentRunLog.created_at) < run_cutoff)
        )
        session.commit()

    logger.info(
        "Log retention cleanup: removed %d event log(s) and %d run log(s)",
        event_result.rowcount,
        run_result.rowcount,
    )


async def register_cleanup_jobs() -> None:
    """Schedule the daily log-retention job. Safe to call even if the scheduler
    already has the job registered (conflicts are silently ignored)."""
    from apscheduler.triggers.interval import IntervalTrigger

    from app.scheduler.engine import get_scheduler

    scheduler = get_scheduler()
    if scheduler is None:
        logger.warning("Scheduler not running — cleanup jobs will not be registered")
        return

    try:
        await scheduler.add_schedule(
            purge_old_logs,
            IntervalTrigger(hours=24),
            id=_CLEANUP_JOB_ID,
        )
        logger.debug("Cleanup job registered (id=%s)", _CLEANUP_JOB_ID)
    except Exception:
        # Job already exists from a previous run with a persistent data store, ignore.
        logger.debug("Cleanup job already registered (id=%s)", _CLEANUP_JOB_ID, exc_info=True)
