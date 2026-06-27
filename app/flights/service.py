from __future__ import annotations

import hashlib
import json
import logging
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.flights.models import FlightWatch

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Provider factory
# ---------------------------------------------------------------------------

def get_provider() -> object:
    from app.config import get_settings
    from app.flights.providers.aerodatabox import AeroDataBoxProvider

    settings = get_settings()
    if settings.flight_provider != "aerodatabox":
        raise ValueError(f"Unknown flight provider: {settings.flight_provider!r}")

    return AeroDataBoxProvider(
        rapidapi_key=settings.flight_aerodatabox_rapidapi_key,
        rapidapi_host=settings.flight_aerodatabox_rapidapi_host,
        base_url=settings.flight_aerodatabox_base_url,
        alerts_enabled=settings.flight_aerodatabox_alerts_enabled,
    )


# ---------------------------------------------------------------------------
# track_flight — called by the agent tool
# ---------------------------------------------------------------------------

async def track_flight(
    user_id: str,
    household_id: str,
    channel_user_id: str,
    carrier_code: str,
    flight_number: str,
    departure_date_str: str,
    origin: str | None = None,
    destination: str | None = None,
    label: str | None = None,
    notify_policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve and persist a new flight watch. Returns a result dict."""
    from datetime import date

    from app.config import get_settings
    from app.flights.models import DEFAULT_NOTIFY_POLICY, FlightWatch
    from app.flights.providers.base import (
        FlightQuery,
        ProviderAlertDeferredError,
        ProviderAmbiguousFlightError,
        ProviderError,
        ProviderFlightNotFoundError,
        ProviderQuotaError,
    )
    from app.flights.repository import save_watch

    settings = get_settings()

    try:
        dep_date = date.fromisoformat(departure_date_str)
    except ValueError:
        return {
            "ok": False,
            "error": f"Invalid departure_date: {departure_date_str!r}. Use YYYY-MM-DD.",
        }

    provider = get_provider()

    query = FlightQuery(
        carrier_code=carrier_code.upper(),
        flight_number=flight_number,
        departure_date=dep_date,
        origin=origin,
        destination=destination,
    )

    try:
        from app.flights.providers.base import FlightProvider
        assert isinstance(provider, FlightProvider)
        candidates = await provider.resolve_flight(query)
    except ProviderFlightNotFoundError as exc:
        return {"ok": False, "error": str(exc)}
    except ProviderAmbiguousFlightError as exc:
        return {
            "ok": False,
            "error": "Multiple matching flights found. Please clarify origin/destination.",
            "candidates": [
                {
                    "provider_flight_id": c.provider_flight_id,
                    "origin": c.origin,
                    "destination": c.destination,
                    "departure_date": str(c.departure_date),
                }
                for c in exc.candidates
            ],
        }
    except ProviderQuotaError:
        return {"ok": False, "error": "Flight data provider temporarily unavailable (quota)."}
    except ProviderError as exc:
        return {"ok": False, "error": f"Provider error: {exc}"}

    if not candidates:
        return {"ok": False, "error": "No matching flights found."}

    resolved = candidates[0]

    # Generate a high-entropy webhook token; store only its hash
    raw_token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()

    # Build the public webhook URL for this watch
    public_base = settings.flight_webhook_public_base_url.rstrip("/")
    webhook_url = f"{public_base}/webhook/flights/{provider.name}/{raw_token}"

    effective_policy = {**DEFAULT_NOTIFY_POLICY, **(notify_policy or {})}

    # Monitoring window: start now, end at scheduled_arrival + offset
    now = datetime.now(timezone.utc)
    monitoring_ends_at = now + timedelta(
        days=2,  # default fallback if no arrival time yet
        hours=settings.flight_monitor_ends_hours_after_arrival,
    )

    watch = FlightWatch(
        id=str(uuid.uuid4()),
        household_id=household_id,
        user_id=user_id,
        channel_user_id=channel_user_id,
        label=label,
        carrier_code=resolved.carrier_code or carrier_code.upper(),
        flight_number=resolved.flight_number or flight_number,
        scheduled_departure_date=resolved.departure_date,
        origin=resolved.origin or origin,
        destination=resolved.destination or destination,
        operating_carrier_code=resolved.operating_carrier_code,
        marketing_carrier_code=resolved.marketing_carrier_code,
        codeshares=resolved.codeshares,
        aircraft_type=resolved.aircraft_type,
        tail_number=resolved.tail_number,
        status="ACTIVE",
        provider=provider.name,
        provider_flight_id=resolved.provider_flight_id,
        webhook_token_hash=token_hash,
        monitoring_starts_at=now,
        monitoring_ends_at=monitoring_ends_at,
        notify_policy=effective_policy,
        created_at=now,
        updated_at=now,
    )
    save_watch(watch)
    _emit_admin_event("flight.watch_created", {"watch_id": watch.id, "flight": watch.flight_label})

    # Attempt alert subscription
    alert_status = "no_alert"
    if settings.flight_aerodatabox_alerts_enabled and public_base:
        try:
            from app.flights.providers.base import FlightProvider
            assert isinstance(provider, FlightProvider)
            alert = await provider.create_alert(watch, webhook_url)
            watch.provider_alert_id = alert.alert_id
            watch.provider_subscription_kind = alert.subscription_kind
            save_watch(watch)
            _emit_admin_event("flight.provider_alert_created", {
                "watch_id": watch.id,
                "alert_id": alert.alert_id,
            })
            alert_status = "active"
        except ProviderAlertDeferredError:
            alert_status = "deferred"
            _emit_admin_event("flight.provider_alert_deferred", {"watch_id": watch.id})
        except ProviderError as exc:
            alert_status = "failed"
            logger.warning("Alert subscription failed for watch %s: %s", watch.id, exc)

    # Fetch initial status snapshot
    try:
        from app.flights.providers.base import FlightProvider
        assert isinstance(provider, FlightProvider)
        snapshot = await provider.get_status(resolved.provider_flight_id, query)
        snapshot.watch_id = watch.id
        from app.flights.repository import save_snapshot
        save_snapshot(snapshot)
        _emit_admin_event("flight.status_refreshed", {"watch_id": watch.id})
        initial_status = snapshot.to_summary_dict()
    except ProviderError as exc:
        logger.warning("Initial status fetch failed for watch %s: %s", watch.id, exc)
        initial_status = {"note": "Initial status fetch failed — will retry on next poll cycle."}

    return {
        "ok": True,
        "watch_id": watch.id,
        "flight": watch.flight_label,
        "status": "ACTIVE",
        "alert_subscription": alert_status,
        "alert_deferred_note": (
            f"Alert subscription will be activated closer to departure "
            f"(within {settings.flight_subscription_retry_lead_days} days). "
            "Polling is active in the meantime."
            if alert_status == "deferred" else None
        ),
        "initial_status": initial_status,
    }


# ---------------------------------------------------------------------------
# get_flight_status — called by the agent tool
# ---------------------------------------------------------------------------

async def get_flight_status(
    user_id: str,
    watch_id: str | None = None,
    carrier_code: str | None = None,
    flight_number: str | None = None,
    departure_date_str: str | None = None,
) -> dict[str, Any]:
    from app.flights.providers.base import (
        FlightProvider,
        FlightQuery,
        ProviderError,
        ProviderFlightNotFoundError,
        ProviderQuotaError,
    )
    from app.flights.repository import (
        get_latest_snapshot,
        get_watch,
        list_watches_for_user,
        save_snapshot,
    )

    provider = get_provider()
    assert isinstance(provider, FlightProvider)

    # Resolve watch
    watch = None
    if watch_id:
        watch = get_watch(watch_id)
    elif not any([carrier_code, flight_number]):
        # Try to find the single upcoming watch for this user
        active = list_watches_for_user(user_id, include_terminal=False)
        if len(active) == 1:
            watch = active[0]
        elif len(active) > 1:
            from app.flights.models import FlightWatch
            return {
                "ok": False,
                "error": "Multiple active watches found. Please specify a flight.",
                "watches": [
                    {
                        "watch_id": w.id,
                        "flight": w.flight_label,
                    }
                    for w in active
                    if isinstance(w, FlightWatch)
                ],
            }
        else:
            return {"ok": False, "error": "No active flight watches found."}

    if watch is None and carrier_code and flight_number:
        # Ad hoc lookup without a persistent watch
        from datetime import date

        from app.flights.models import FlightWatch

        if departure_date_str:
            try:
                dep_date = date.fromisoformat(departure_date_str)
            except ValueError:
                return {"ok": False, "error": f"Invalid date: {departure_date_str!r}"}
        else:
            dep_date = datetime.now(timezone.utc).date()

        query = FlightQuery(
            carrier_code=carrier_code.upper(),
            flight_number=flight_number,
            departure_date=dep_date,
        )
        try:
            snapshot = await provider.get_status(None, query)
        except ProviderQuotaError:
            return {"ok": False, "error": "Flight data provider temporarily unavailable (quota)."}
        except ProviderError as exc:
            return {"ok": False, "error": str(exc)}

        return {"ok": True, "ad_hoc": True, "status": snapshot.to_summary_dict()}

    if watch is None:
        return {"ok": False, "error": "Could not find a matching flight watch."}

    from app.flights.models import FlightWatch

    assert isinstance(watch, FlightWatch)

    # Refresh status
    query = FlightQuery(
        carrier_code=watch.carrier_code,
        flight_number=watch.flight_number,
        departure_date=watch.scheduled_departure_date,
        origin=watch.origin,
        destination=watch.destination,
    )
    try:
        snapshot = await provider.get_status(watch.provider_flight_id, query)
        snapshot.watch_id = watch.id
        import hashlib

        snapshot.id = hashlib.sha1(
            f"{watch.id}:{snapshot.fetched_at.isoformat()}".encode()
        ).hexdigest()[:24]
        save_snapshot(snapshot)
        watch.consecutive_provider_errors = 0
        from app.flights.repository import save_watch
        save_watch(watch)
        _emit_admin_event("flight.status_refreshed", {"watch_id": watch.id})
    except ProviderQuotaError:
        snapshot_obj = get_latest_snapshot(watch.id)
        stale_note = "Flight data provider quota exceeded. Showing last known status."
        if snapshot_obj is None:
            return {"ok": False, "error": stale_note}
        from app.flights.models import FlightStatusSnapshot
        assert isinstance(snapshot_obj, FlightStatusSnapshot)
        result = snapshot_obj.to_summary_dict()
        result["stale"] = True
        result["stale_note"] = stale_note
        return {"ok": True, "watch_id": watch.id, "flight": watch.flight_label, "status": result}
    except ProviderFlightNotFoundError:
        # 404 from provider. If departure date is in the past the flight has
        # likely completed and dropped out of the live feed — returning the
        # stale pre-departure "SCHEDULED" snapshot would be misleading.
        today = datetime.now(timezone.utc).date()
        if watch.scheduled_departure_date < today:
            return {
                "ok": True,
                "watch_id": watch.id,
                "flight": watch.flight_label,
                "status": {
                    "state": "UNKNOWN",
                    "stale": True,
                    "stale_note": (
                        "Flight date has passed and the provider no longer has live data "
                        "for this flight. The flight likely completed normally."
                    ),
                },
            }
        # Departure date is today or future — treat as a transient API blip.
        snapshot_obj = get_latest_snapshot(watch.id)
        if snapshot_obj is None:
            return {"ok": False, "error": "Flight not found by provider."}
        from app.flights.models import FlightStatusSnapshot
        assert isinstance(snapshot_obj, FlightStatusSnapshot)
        result = snapshot_obj.to_summary_dict()
        result["stale"] = True
        result["stale_note"] = "Provider returned no data. Showing last known status."
        return {"ok": True, "watch_id": watch.id, "flight": watch.flight_label, "status": result}
    except ProviderError as exc:
        watch.consecutive_provider_errors += 1
        from app.flights.repository import save_watch
        save_watch(watch)
        _emit_admin_event("flight.provider_error", {
            "watch_id": watch.id,
            "error": str(exc),
            "consecutive": watch.consecutive_provider_errors,
        })
        snapshot_obj = get_latest_snapshot(watch.id)
        if snapshot_obj is None:
            return {"ok": False, "error": f"Provider error: {exc}"}
        from app.flights.models import FlightStatusSnapshot
        assert isinstance(snapshot_obj, FlightStatusSnapshot)
        result = snapshot_obj.to_summary_dict()
        result["stale"] = True
        return {"ok": True, "watch_id": watch.id, "flight": watch.flight_label, "status": result}

    return {
        "ok": True,
        "watch_id": watch.id,
        "flight": watch.flight_label,
        "status": snapshot.to_summary_dict(),
    }


# ---------------------------------------------------------------------------
# cancel_flight_watch — called by the agent tool
# ---------------------------------------------------------------------------

async def cancel_flight_watch(user_id: str, watch_id: str) -> dict[str, Any]:
    from app.flights.models import WATCH_CANCELLED, FlightWatch
    from app.flights.repository import get_watch, save_watch

    watch = get_watch(watch_id)
    if watch is None:
        return {"ok": False, "error": f"Watch {watch_id!r} not found."}

    assert isinstance(watch, FlightWatch)

    if watch.user_id != user_id:
        return {"ok": False, "error": "Not authorized."}

    if watch.is_terminal:
        return {"ok": False, "error": f"Watch is already {watch.status}."}

    watch.status = WATCH_CANCELLED
    watch.status_reason = "user_cancelled"
    watch.completed_at = datetime.now(timezone.utc)
    save_watch(watch)

    await _cleanup_terminal_watch(watch)

    return {"ok": True, "message": f"Stopped monitoring {watch.flight_label}."}


# ---------------------------------------------------------------------------
# Webhook ingestion — called from the webhook handler
# ---------------------------------------------------------------------------

async def ingest_webhook(
    vendor: str,
    webhook_token: str,
    headers: dict[str, str],
    body: bytes,
) -> dict[str, Any]:
    from app.flights.diff import compute_changes, should_notify
    from app.flights.models import WATCH_CANCELLED, FlightWatch
    from app.flights.notifications import dispatch_flight_update
    from app.flights.providers.base import FlightProvider, ProviderError
    from app.flights.repository import (
        event_hash_exists,
        get_latest_snapshot,
        get_watch_by_token_hash,
        save_event,
        save_snapshot,
        save_watch,
    )

    # Resolve watch by hashing the token
    token_hash = hashlib.sha256(webhook_token.encode()).hexdigest()
    watch = get_watch_by_token_hash(token_hash)
    if watch is None:
        logger.warning("Flight webhook: token hash not found")
        _emit_admin_event("flight.webhook_rejected", {"reason": "token_not_found"})
        return {"ok": False, "reason": "not_found"}

    assert isinstance(watch, FlightWatch)

    if watch.provider != vendor:
        logger.warning("Flight webhook: vendor mismatch for watch %s", watch.id)
        _emit_admin_event(
            "flight.webhook_rejected", {"reason": "vendor_mismatch", "watch_id": watch.id}
        )
        return {"ok": False, "reason": "vendor_mismatch"}

    provider = get_provider()
    assert isinstance(provider, FlightProvider)

    if not provider.verify_webhook(headers, body, webhook_token):
        _emit_admin_event(
            "flight.webhook_rejected", {"reason": "signature_invalid", "watch_id": watch.id}
        )
        return {"ok": False, "reason": "invalid_signature"}

    # Parse raw event
    try:
        normalized = provider.normalize_webhook(body)
    except ProviderError as exc:
        logger.warning("Flight webhook normalize failed for watch %s: %s", watch.id, exc)
        return {"ok": False, "reason": "parse_error"}

    # Deduplication
    event_hash = hashlib.sha256(body).hexdigest()
    if event_hash_exists(event_hash):
        _emit_admin_event("flight.event_duplicate", {"watch_id": watch.id})
        return {"ok": True, "duplicate": True}

    now = datetime.now(timezone.utc)

    from app.flights.models import FlightEvent
    event = FlightEvent(
        id=str(uuid.uuid4()),
        watch_id=watch.id,
        provider=vendor,
        provider_event_id=normalized.get("provider_event_id"),
        event_hash=event_hash,
        event_type=normalized.get("event_type", "status_update"),
        severity="info",
        received_at=now,
        raw_json=body.decode("utf-8", errors="replace"),
        normalized_json=json.dumps(normalized),
    )
    save_event(event)
    _emit_admin_event("flight.webhook_received", {"watch_id": watch.id})

    # Fetch fresh status after webhook (event payload may be incomplete)
    from app.flights.providers.base import FlightQuery
    query = FlightQuery(
        carrier_code=watch.carrier_code,
        flight_number=watch.flight_number,
        departure_date=watch.scheduled_departure_date,
        origin=watch.origin,
        destination=watch.destination,
    )
    try:
        snapshot = await provider.get_status(watch.provider_flight_id, query)
        snapshot.watch_id = watch.id
        snapshot.id = hashlib.sha1(
            f"{watch.id}:{snapshot.fetched_at.isoformat()}".encode()
        ).hexdigest()[:24]
    except ProviderError as exc:
        logger.warning("Failed to fetch status after webhook for watch %s: %s", watch.id, exc)
        _emit_admin_event("flight.provider_error", {"watch_id": watch.id, "source": "webhook"})
        # Schedule an immediate poll so the next watchdog cycle doesn't wait
        # for the full suppress window before picking up this event.
        import asyncio
        asyncio.create_task(poll_watch(watch.id, force=True))
        return {"ok": True, "note": "status_fetch_failed"}

    # Diff
    from app.flights.models import FlightStatusSnapshot as _FSS
    _prev_raw = get_latest_snapshot(watch.id)
    previous = _prev_raw if isinstance(_prev_raw, _FSS) else None
    snapshot.fetch_source = "webhook"
    save_snapshot(snapshot)
    _emit_admin_event("flight.status_refreshed", {"watch_id": watch.id, "source": "webhook"})

    changes = compute_changes(watch, previous, snapshot, watch.notify_policy)

    if not changes:
        _emit_admin_event(
            "flight.notify_suppressed", {"watch_id": watch.id, "reason": "no_changes"}
        )
        return {"ok": True}

    # Check for airline cancellation → transition to CANCELLED
    if snapshot.cancelled and watch.status == "ACTIVE":
        watch.status = WATCH_CANCELLED
        watch.status_reason = "airline_cancelled"
        watch.completed_at = now
        save_watch(watch)
        await _cleanup_terminal_watch(watch)
        import asyncio

        from app.flights.notifications import notify_watch_cancelled
        asyncio.create_task(notify_watch_cancelled(watch, reason="airline"))
        _emit_admin_event(
            "flight.watch_cancelled", {"watch_id": watch.id, "reason": "airline_cancelled"}
        )
        return {"ok": True}

    # Filter by policy and dispatch
    notifiable = [c for c in changes if should_notify(c, watch.notify_policy)]
    if notifiable:
        import asyncio
        asyncio.create_task(dispatch_flight_update(watch, notifiable))
        _emit_admin_event(
            "flight.agent_triggered",
            {"watch_id": watch.id, "changes": len(notifiable)},
        )
    else:
        _emit_admin_event(
            "flight.notify_suppressed", {"watch_id": watch.id, "reason": "policy"}
        )

    return {"ok": True}


# ---------------------------------------------------------------------------
# Poll a single watch — called from scheduler
# ---------------------------------------------------------------------------

async def poll_watch(watch_id: str, *, force: bool = False) -> None:
    from app.config import get_settings
    from app.flights.diff import compute_changes, should_notify
    from app.flights.models import WATCH_FAILED, FlightWatch
    from app.flights.notifications import dispatch_flight_update, notify_watch_failed
    from app.flights.providers.base import (
        FlightProvider,
        FlightQuery,
        ProviderError,
        ProviderQuotaError,
    )
    from app.flights.repository import (
        get_latest_snapshot,
        get_watch,
        save_snapshot,
        save_watch,
    )

    settings = get_settings()
    watch = get_watch(watch_id)
    if watch is None or not isinstance(watch, FlightWatch):
        return
    if watch.is_terminal:
        return

    # Skip if webhook refreshed recently (bypass when force=True, e.g. webhook fetch failed)
    from app.flights.models import FlightStatusSnapshot as _FSS2
    _last_raw = get_latest_snapshot(watch.id)
    last_snapshot = _last_raw if isinstance(_last_raw, _FSS2) else None
    if last_snapshot and not force:
        suppress_window = timedelta(minutes=settings.flight_poll_recent_webhook_suppress_minutes)
        if datetime.now(timezone.utc) - last_snapshot.fetched_at < suppress_window:
            _emit_admin_event("flight.poll_skipped", {
                "watch_id": watch.id,
                "reason": "recent_webhook",
            })
            return

    query = FlightQuery(
        carrier_code=watch.carrier_code,
        flight_number=watch.flight_number,
        departure_date=watch.scheduled_departure_date,
        origin=watch.origin,
        destination=watch.destination,
    )

    provider = get_provider()
    assert isinstance(provider, FlightProvider)

    try:
        snapshot = await provider.get_status(watch.provider_flight_id, query)
        snapshot.watch_id = watch.id
        snapshot.id = hashlib.sha1(
            f"{watch.id}:{snapshot.fetched_at.isoformat()}".encode()
        ).hexdigest()[:24]
        watch.consecutive_provider_errors = 0
    except ProviderQuotaError:
        # Quota is transient — do not increment error counter
        _emit_admin_event("flight.provider_quota_exceeded", {"watch_id": watch.id})
        return
    except ProviderError:
        watch.consecutive_provider_errors += 1
        save_watch(watch)
        _emit_admin_event("flight.provider_error", {
            "watch_id": watch.id,
            "consecutive": watch.consecutive_provider_errors,
        })
        if watch.consecutive_provider_errors >= settings.flight_watch_fail_consecutive_errors:
            from app.flights.models import FlightStatusSnapshot
            _last_raw2 = get_latest_snapshot(watch.id)
            last = _last_raw2 if isinstance(_last_raw2, FlightStatusSnapshot) else None
            last_state = last.state if last else None
            watch.status = WATCH_FAILED
            watch.status_reason = "consecutive_provider_errors"
            watch.completed_at = datetime.now(timezone.utc)
            save_watch(watch)
            await _cleanup_terminal_watch(watch)
            import asyncio
            asyncio.create_task(notify_watch_failed(watch, last_state))
            _emit_admin_event("flight.watch_failed", {"watch_id": watch.id})
        return

    from app.flights.models import FlightStatusSnapshot as _FSS3
    _prev_raw2 = get_latest_snapshot(watch.id)
    previous = _prev_raw2 if isinstance(_prev_raw2, _FSS3) else None
    snapshot.fetch_source = "poll"
    save_snapshot(snapshot)
    save_watch(watch)
    _emit_admin_event("flight.status_refreshed", {"watch_id": watch.id, "source": "poll"})

    changes = compute_changes(watch, previous, snapshot, watch.notify_policy)
    if changes:
        notifiable = [c for c in changes if should_notify(c, watch.notify_policy)]
        if notifiable:
            import asyncio
            asyncio.create_task(dispatch_flight_update(watch, notifiable))
            _emit_admin_event("flight.agent_triggered", {"watch_id": watch.id})

    # Check monitoring_ends_at — complete the watch if window has passed
    if watch.monitoring_ends_at and datetime.now(timezone.utc) >= watch.monitoring_ends_at:
        from app.flights.models import WATCH_COMPLETED
        watch.status = WATCH_COMPLETED
        watch.status_reason = "monitoring_window_elapsed"
        watch.completed_at = datetime.now(timezone.utc)
        save_watch(watch)
        await _cleanup_terminal_watch(watch)
        _emit_admin_event("flight.watch_completed", {"watch_id": watch.id})


# ---------------------------------------------------------------------------
# Check alert credit balance
# ---------------------------------------------------------------------------

async def check_alert_credit_balance() -> None:
    from app.config import get_settings
    from app.flights.providers.base import FlightProvider, ProviderError

    settings = get_settings()
    if not settings.flight_aerodatabox_alerts_enabled:
        return

    provider = get_provider()
    assert isinstance(provider, FlightProvider)

    try:
        balance = await provider.get_alert_credit_balance()
    except ProviderError as exc:
        logger.debug("Could not check alert credit balance: %s", exc)
        return

    if balance.empty:
        _emit_admin_event("flight.alert_credit_empty", {"remaining": 0})
    elif balance.low:
        _emit_admin_event("flight.alert_credit_low", {"remaining": balance.remaining})


# ---------------------------------------------------------------------------
# Retention cleanup
# ---------------------------------------------------------------------------

async def run_retention_cleanup() -> None:
    from app.config import get_settings
    from app.flights.repository import delete_old_events, delete_old_terminal_watches

    settings = get_settings()
    events_deleted = delete_old_events(settings.flight_raw_event_retention_days)
    watches_deleted = delete_old_terminal_watches(settings.flight_completed_watch_retention_days)
    _emit_admin_event("flight.retention_cleanup_completed", {
        "events_deleted": events_deleted,
        "watches_deleted": watches_deleted,
    })


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

async def _cleanup_terminal_watch(watch: "FlightWatch") -> None:
    """Remove polling jobs and delete provider alert subscription on terminal transition."""
    from app.flights.providers.base import FlightProvider, ProviderError

    # Remove scheduler polling jobs
    try:
        from app.flights.scheduler import remove_watch_jobs
        remove_watch_jobs(watch.id)
    except Exception as exc:
        logger.warning("Could not remove scheduler jobs for watch %s: %s", watch.id, exc)

    # Delete provider alert subscription
    if watch.provider_alert_id:
        try:
            provider = get_provider()
            assert isinstance(provider, FlightProvider)
            await provider.delete_alert(watch.provider_alert_id)
            _emit_admin_event("flight.provider_alert_deleted", {
                "watch_id": watch.id,
                "alert_id": watch.provider_alert_id,
            })
        except ProviderError as exc:
            logger.warning(
                "Failed to delete provider alert %s for watch %s: %s",
                watch.provider_alert_id, watch.id, exc,
            )


def _emit_admin_event(event_type: str, payload: dict[str, Any]) -> None:
    try:
        from app.control.admin_events import emit_admin_event
        emit_admin_event(event_type, payload)
    except Exception:
        pass
