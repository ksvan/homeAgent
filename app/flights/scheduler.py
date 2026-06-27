from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

# Scheduler job IDs
_WATCHDOG_JOB_ID = "flight_watchdog"
_ALERT_RETRY_JOB_ID = "flight_alert_subscription_retry"
_CREDIT_CHECK_JOB_ID = "flight_alert_credit_check"
_RETENTION_JOB_ID = "flight_retention_cleanup"


# ---------------------------------------------------------------------------
# Watchdog — runs every 15 minutes, decides which watches to poll
# ---------------------------------------------------------------------------


async def flight_watchdog_job() -> None:
    """Check all active flight watches and poll those whose interval has elapsed."""

    from app.flights.repository import get_latest_snapshot, list_active_watches
    from app.flights.service import poll_watch

    watches = list_active_watches()
    logger.info("Watchdog: %d active watches", len(watches))
    if not watches:
        return

    now = datetime.now(timezone.utc)
    from app.flights.models import FlightWatch

    for watch in watches:
        if not isinstance(watch, FlightWatch):
            continue

        dep_dt = _departure_datetime(watch, now)
        if dep_dt is None:
            logger.info("Watchdog: skipping %s — no departure time", watch.id)
            continue

        delta = dep_dt - now
        poll_interval = _poll_interval_for_delta(delta)
        if poll_interval is None:
            logger.info(
                "Watchdog: skipping %s — too far out (%.1fh)",
                watch.id,
                delta.total_seconds() / 3600,
            )
            continue

        # Check when the snapshot was last fetched
        from app.flights.models import FlightStatusSnapshot as _FSS

        _last_raw = get_latest_snapshot(watch.id)
        last = _last_raw if isinstance(_last_raw, _FSS) else None
        if last:
            fetched_at = last.fetched_at
            if fetched_at.tzinfo is None:
                fetched_at = fetched_at.replace(tzinfo=timezone.utc)
            age = now - fetched_at
            if age < poll_interval:
                logger.info(
                    "Watchdog: skipping %s — fetched %.0fm ago (interval %s)",
                    watch.id,
                    age.total_seconds() / 60,
                    poll_interval,
                )
                continue

        logger.info("Watchdog: polling watch %s", watch.id)
        try:
            await poll_watch(watch.id)
        except Exception:
            logger.exception("Watchdog: poll_watch failed for watch %s", watch.id)
        await asyncio.sleep(3)


def _departure_datetime(watch: object, now: datetime) -> datetime | None:
    """Best-effort departure datetime from watch. Falls back to midnight on departure date."""
    from app.flights.models import FlightStatusSnapshot, FlightWatch
    from app.flights.repository import get_latest_snapshot

    assert isinstance(watch, FlightWatch)
    snap = get_latest_snapshot(watch.id)
    if snap and isinstance(snap, FlightStatusSnapshot):
        candidate = snap.estimated_off or snap.scheduled_off
        if candidate:
            if candidate.tzinfo is None:
                candidate = candidate.replace(tzinfo=timezone.utc)
            return candidate

    # Use scheduled departure date at midnight UTC as a fallback
    from datetime import datetime as dt

    return dt(
        watch.scheduled_departure_date.year,
        watch.scheduled_departure_date.month,
        watch.scheduled_departure_date.day,
        tzinfo=timezone.utc,
    )


def _poll_interval_for_delta(delta: timedelta) -> timedelta | None:
    """Return the polling interval for a given time-to-departure, or None to skip."""
    hours = delta.total_seconds() / 3600

    if hours > 72:
        return None  # webhooks only beyond 3 days out
    if hours > 48:
        return timedelta(hours=12)  # light fallback poll in 48–72h window
    if hours > 12:
        return timedelta(hours=6)
    if hours > 4:
        return timedelta(hours=2)
    if hours > 1:
        return timedelta(minutes=30)
    # < 1 hour before departure or already departed
    if delta.total_seconds() > -3600 * 12:  # within 12 hours after expected departure
        return timedelta(minutes=15)
    return None  # too long after departure — monitoring_ends_at handles cleanup


# ---------------------------------------------------------------------------
# Deferred alert subscription retry
# ---------------------------------------------------------------------------


async def alert_subscription_retry_job() -> None:
    """Retry creating alert subscriptions for watches that were deferred at creation."""
    from app.config import get_settings
    from app.flights.providers.base import (
        FlightProvider,
        ProviderAlertDeferredError,
        ProviderError,
    )
    from app.flights.repository import (
        list_active_watches_pending_subscription,
        save_watch,
    )
    from app.flights.service import _emit_admin_event, get_provider

    settings = get_settings()
    if not settings.flight_aerodatabox_alerts_enabled:
        return

    watches = list_active_watches_pending_subscription(settings.flight_subscription_retry_lead_days)
    if not watches:
        return

    provider = get_provider()
    assert isinstance(provider, FlightProvider)

    public_base = settings.flight_webhook_public_base_url.rstrip("/")
    if not public_base:
        logger.warning("FLIGHT_WEBHOOK_PUBLIC_BASE_URL not set — cannot retry alert subscriptions")
        return

    for watch in watches:
        from app.flights.models import FlightWatch

        if not isinstance(watch, FlightWatch):
            continue

        import hashlib
        import secrets

        # Generate a new token for this watch since we don't have the original plaintext
        raw_token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
        webhook_url = f"{public_base}/webhook/flights/{provider.name}/{raw_token}"

        await asyncio.sleep(3)
        try:
            alert = await provider.create_alert(watch, webhook_url)
            watch.provider_alert_id = alert.alert_id
            watch.provider_subscription_kind = alert.subscription_kind
            watch.webhook_token_hash = token_hash
            save_watch(watch)
            _emit_admin_event(
                "flight.provider_alert_created",
                {
                    "watch_id": watch.id,
                    "alert_id": alert.alert_id,
                    "source": "retry",
                },
            )
            logger.info("Alert subscription created for watch %s (retry)", watch.id)
        except ProviderAlertDeferredError:
            # Still too far out — will retry again tomorrow
            pass
        except ProviderError as exc:
            logger.warning("Alert retry failed for watch %s: %s", watch.id, exc)
            # If within 24 hours of departure, emit a failure event
            from datetime import datetime as dt

            dep = dt(
                watch.scheduled_departure_date.year,
                watch.scheduled_departure_date.month,
                watch.scheduled_departure_date.day,
                tzinfo=timezone.utc,
            )
            if (dep - datetime.now(timezone.utc)).total_seconds() < 86400:
                _emit_admin_event("flight.provider_alert_failed", {"watch_id": watch.id})


# ---------------------------------------------------------------------------
# Alert credit balance check
# ---------------------------------------------------------------------------


async def alert_credit_check_job() -> None:
    """Daily check of AeroDataBox alert credit balance."""
    from app.flights.service import check_alert_credit_balance

    await check_alert_credit_balance()


# ---------------------------------------------------------------------------
# Retention cleanup
# ---------------------------------------------------------------------------


async def flight_retention_job() -> None:
    """Daily retention cleanup for old flight events and terminal watch records."""
    from app.flights.service import run_retention_cleanup

    await run_retention_cleanup()


# ---------------------------------------------------------------------------
# Registration and startup restore
# ---------------------------------------------------------------------------


async def register_flight_scheduler_jobs() -> None:
    """Register all flight-related APScheduler jobs. Safe to call on startup."""
    from apscheduler.triggers.interval import IntervalTrigger

    from app.scheduler.engine import get_scheduler

    scheduler = get_scheduler()
    if scheduler is None:
        logger.warning("Scheduler not running — flight jobs will not be registered")
        return

    jobs = [
        (flight_watchdog_job, IntervalTrigger(minutes=15), _WATCHDOG_JOB_ID),
        (alert_subscription_retry_job, IntervalTrigger(hours=2), _ALERT_RETRY_JOB_ID),
        (alert_credit_check_job, IntervalTrigger(hours=24), _CREDIT_CHECK_JOB_ID),
        (flight_retention_job, IntervalTrigger(hours=24), _RETENTION_JOB_ID),
    ]

    for fn, trigger, job_id in jobs:
        try:
            await scheduler.add_schedule(fn, trigger, id=job_id)
            logger.debug("Flight scheduler job registered: %s", job_id)
        except Exception:
            logger.debug("Flight scheduler job already registered: %s", job_id, exc_info=True)

    # Run subscription retry immediately on startup so any deferred or failed
    # subscriptions are picked up without waiting for the first 2h interval.
    try:
        await alert_subscription_retry_job()
    except Exception:
        logger.warning("Startup alert subscription retry failed", exc_info=True)


def remove_watch_jobs(watch_id: str) -> None:
    """Remove all per-watch scheduler jobs (currently handled by watchdog pattern)."""
    # In the centralized watchdog pattern there are no per-watch jobs to remove.
    # This function is called by the service on terminal transitions — it is a
    # no-op here but exists for future per-watch job support.
    pass
