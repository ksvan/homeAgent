"""
Email channel background worker.

Two scheduled jobs:
  - retry_job    (every 2 min)  — re-queues FAILED_RETRYABLE rows whose
                                   next_attempt_at has elapsed
  - stale_lock_job (every 5 min) — releases locked rows that have been
                                   stuck in CLASSIFYING/PROCESSING too long
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlmodel import col, select

from app.db import cache_session
from app.email.models import EmailMessage

logger = logging.getLogger(__name__)

_MAX_ATTEMPTS = 3
_RETRY_BASE_SECONDS = 60  # first retry after 60 s; doubles each attempt
_STALE_LOCK_MINUTES = 10  # locks older than this are released

_RETRY_JOB_ID = "email_retry_worker"
_STALE_LOCK_JOB_ID = "email_stale_lock_sweeper"
_RETENTION_JOB_ID = "email_retention_cleanup"


def _emit(event_type: str, payload: dict[str, object]) -> None:
    try:
        from app.control.admin_events import emit_admin_event

        emit_admin_event(event_type, payload)
    except Exception:
        pass


async def retry_job() -> None:
    """Re-queue FAILED_RETRYABLE rows whose next_attempt_at has elapsed."""
    now = datetime.now(timezone.utc)

    with cache_session() as session:
        rows = session.exec(
            select(EmailMessage).where(
                EmailMessage.status == "FAILED_RETRYABLE",
                col(EmailMessage.next_attempt_at) <= now,
            )
        ).all()

    if not rows:
        return

    logger.info("Email retry worker: %d row(s) eligible for retry", len(rows))

    import asyncio

    from app.email.service import process_email_message

    for row in rows:
        with cache_session() as session:
            live = session.exec(select(EmailMessage).where(EmailMessage.id == row.id)).first()
            if live is None or live.status != "FAILED_RETRYABLE":
                continue
            live.status = "RECEIVED"
            live.locked_at = None
            live.updated_at = now
            session.add(live)
            session.commit()
            session.expunge(live)
            cloned = EmailMessage(
                id=live.id,
                provider=live.provider,
                provider_event_id=live.provider_event_id,
                provider_delivery_id=live.provider_delivery_id,
                provider_message_id=live.provider_message_id,
                provider_thread_id=live.provider_thread_id,
                provider_inbox_id=live.provider_inbox_id,
                household_id=live.household_id,
                user_id=live.user_id,
                channel_user_id=live.channel_user_id,
                from_email=live.from_email,
                subject=live.subject,
                status="RECEIVED",
                attempt_count=live.attempt_count,
            )

        _emit("email.retry_scheduled", {"email_message_id": row.id, "attempt": row.attempt_count})

        def _done(fut: asyncio.Future, mid: str = row.id) -> None:  # type: ignore[type-arg]
            if not fut.cancelled() and (exc := fut.exception()):
                logger.error("Email retry failed (id=%s): %s", mid, exc)

        asyncio.create_task(process_email_message(cloned)).add_done_callback(_done)
        await asyncio.sleep(1)


async def stale_lock_job() -> None:
    """Release stale processing leases that have been held too long."""
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=_STALE_LOCK_MINUTES)

    with cache_session() as session:
        rows = session.exec(
            select(EmailMessage).where(
                col(EmailMessage.status).in_(["CLASSIFYING", "PROCESSING"]),
                col(EmailMessage.locked_at) <= cutoff,
            )
        ).all()

        if not rows:
            return

        logger.info("Email stale-lock sweeper: releasing %d stuck row(s)", len(rows))
        now = datetime.now(timezone.utc)
        for row in rows:
            row.status = "FAILED_RETRYABLE"
            row.locked_at = None
            row.updated_at = now
            row.next_attempt_at = _next_attempt(row.attempt_count)
            row.last_error = "stale_lock_released"
            session.add(row)
            _emit(
                "email.retry_scheduled",
                {
                    "email_message_id": row.id,
                    "reason": "stale_lock",
                    "attempt": row.attempt_count,
                },
            )
        session.commit()


async def retention_job() -> None:
    """Delete EmailMessage rows older than the configured retention window."""
    from app.config import get_settings

    settings = get_settings()
    cutoff = datetime.now(timezone.utc) - timedelta(days=settings.email_channel_retention_days)

    with cache_session() as session:
        rows = session.exec(select(EmailMessage).where(EmailMessage.created_at < cutoff)).all()
        if not rows:
            return
        for row in rows:
            session.delete(row)
        session.commit()
        logger.info("Email retention: deleted %d old row(s)", len(rows))


def _next_attempt(attempt_count: int) -> datetime:
    delay = _RETRY_BASE_SECONDS * (2**attempt_count)
    return datetime.now(timezone.utc) + timedelta(seconds=delay)


async def register_email_worker_jobs() -> None:
    """Register all email worker APScheduler jobs. Safe to call on startup."""
    from apscheduler.triggers.interval import IntervalTrigger

    from app.scheduler.engine import get_scheduler

    scheduler = get_scheduler()
    if scheduler is None:
        logger.warning("Scheduler not running — email worker jobs will not be registered")
        return

    jobs = [
        (retry_job, IntervalTrigger(minutes=2), _RETRY_JOB_ID),
        (stale_lock_job, IntervalTrigger(minutes=5), _STALE_LOCK_JOB_ID),
        (retention_job, IntervalTrigger(hours=24), _RETENTION_JOB_ID),
    ]

    for fn, trigger, job_id in jobs:
        try:
            await scheduler.add_schedule(fn, trigger, id=job_id)
            logger.debug("Email worker job registered: %s", job_id)
        except Exception:
            logger.debug("Email worker job already registered: %s", job_id, exc_info=True)
