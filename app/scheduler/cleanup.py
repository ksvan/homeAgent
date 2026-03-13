from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

_CLEANUP_JOB_ID = "cleanup_old_logs"
_MEMORY_PURGE_JOB_ID = "cleanup_stale_memories"
_TASK_PURGE_JOB_ID = "cleanup_old_tasks"
_TASK_RETENTION_DAYS = 8


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


async def purge_stale_memories() -> None:
    """
    Delete episodic memories that have not been used within their importance tier's TTL.

    Freshness is determined by last_used_at when set, falling back to created_at.
    'critical' memories are never purged.
    Runs daily via APScheduler.
    """
    from sqlalchemy import and_, or_
    from sqlmodel import col, select

    from app.config import get_settings
    from app.db import memory_session
    from app.memory.episodic import _delete_from_vec
    from app.models.memory import EpisodicMemory

    settings = get_settings()
    now = datetime.now(timezone.utc)
    total_deleted = 0

    tiers = [
        ("ephemeral", settings.memory_ttl_ephemeral_days),
        ("normal", settings.memory_ttl_normal_days),
        ("important", settings.memory_ttl_important_days),
    ]

    for importance, days in tiers:
        cutoff = now - timedelta(days=days)

        with memory_session() as session:
            stale = session.exec(
                select(EpisodicMemory).where(
                    col(EpisodicMemory.importance) == importance,
                    or_(
                        and_(
                            col(EpisodicMemory.last_used_at).is_not(None),
                            col(EpisodicMemory.last_used_at) < cutoff,
                        ),
                        and_(
                            col(EpisodicMemory.last_used_at).is_(None),
                            col(EpisodicMemory.created_at) < cutoff,
                        ),
                    ),
                )
            ).all()

            for m in stale:
                _delete_from_vec(m.embedding_id)
                session.delete(m)
            session.commit()

        if stale:
            logger.info(
                "Memory purge: removed %d '%s' memor%s (idle > %d days)",
                len(stale),
                importance,
                "ies" if len(stale) != 1 else "y",
                days,
            )
        total_deleted += len(stale)

    if total_deleted == 0:
        logger.debug("Memory purge: nothing to remove")


async def purge_old_tasks() -> None:
    """
    Delete completed/failed/cancelled Task rows older than _TASK_RETENTION_DAYS.
    ACTIVE tasks are never touched. Runs daily via APScheduler.
    """
    from sqlmodel import col, delete

    from app.db import users_session
    from app.models.tasks import Task

    cutoff = datetime.now(timezone.utc) - timedelta(days=_TASK_RETENTION_DAYS)

    with users_session() as session:
        result = session.exec(
            delete(Task).where(
                Task.status != "ACTIVE",
                col(Task.created_at) < cutoff,
            )
        )
        session.commit()

    if result.rowcount:
        logger.info("Task purge: removed %d old task(s)", result.rowcount)
    else:
        logger.debug("Task purge: nothing to remove")


async def register_cleanup_jobs() -> None:
    """Schedule the daily log-retention and memory-purge jobs. Safe to call even if the
    scheduler already has the jobs registered (conflicts are silently ignored)."""
    from apscheduler.triggers.interval import IntervalTrigger

    from app.scheduler.engine import get_scheduler

    scheduler = get_scheduler()
    if scheduler is None:
        logger.warning("Scheduler not running — cleanup jobs will not be registered")
        return

    for fn, job_id in [
        (purge_old_logs, _CLEANUP_JOB_ID),
        (purge_stale_memories, _MEMORY_PURGE_JOB_ID),
        (purge_old_tasks, _TASK_PURGE_JOB_ID),
    ]:
        try:
            await scheduler.add_schedule(fn, IntervalTrigger(hours=24), id=job_id)
            logger.debug("Cleanup job registered (id=%s)", job_id)
        except Exception:
            logger.debug("Cleanup job already registered (id=%s)", job_id, exc_info=True)
