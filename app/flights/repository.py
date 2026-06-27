from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# FlightWatch
# ---------------------------------------------------------------------------


def save_watch(watch: object) -> None:
    from app.db import cache_session
    from app.flights.models import FlightWatch
    from app.models.flights import FlightWatchRow

    assert isinstance(watch, FlightWatch)
    now = datetime.now(timezone.utc)

    with cache_session() as session:
        row = session.get(FlightWatchRow, watch.id)
        if row is None:
            row = FlightWatchRow(id=watch.id)
        row.household_id = watch.household_id
        row.user_id = watch.user_id
        row.channel_user_id = watch.channel_user_id
        row.label = watch.label
        row.carrier_code = watch.carrier_code
        row.flight_number = watch.flight_number
        row.scheduled_departure_date = watch.scheduled_departure_date
        row.origin = watch.origin
        row.destination = watch.destination
        row.operating_carrier_code = watch.operating_carrier_code
        row.marketing_carrier_code = watch.marketing_carrier_code
        row.codeshares_json = json.dumps(watch.codeshares)
        row.aircraft_type = watch.aircraft_type
        row.tail_number = watch.tail_number
        row.status = watch.status
        row.status_reason = watch.status_reason
        row.monitoring_starts_at = watch.monitoring_starts_at
        row.monitoring_ends_at = watch.monitoring_ends_at
        row.provider = watch.provider
        row.provider_flight_id = watch.provider_flight_id
        row.provider_alert_id = watch.provider_alert_id
        row.provider_subscription_kind = watch.provider_subscription_kind
        row.webhook_token_hash = watch.webhook_token_hash
        row.consecutive_provider_errors = watch.consecutive_provider_errors
        row.notify_policy_json = json.dumps(watch.notify_policy)
        row.updated_at = now
        if watch.created_at is None:
            row.created_at = now
        row.completed_at = watch.completed_at
        session.add(row)
        session.commit()


def get_watch(watch_id: str) -> object | None:
    from app.db import cache_session
    from app.models.flights import FlightWatchRow

    with cache_session() as session:
        row = session.get(FlightWatchRow, watch_id)
        if row is None:
            return None
        return _row_to_watch(row)


def get_watch_by_token_hash(token_hash: str) -> object | None:
    from sqlmodel import select

    from app.db import cache_session
    from app.models.flights import FlightWatchRow

    with cache_session() as session:
        row = session.exec(
            select(FlightWatchRow).where(FlightWatchRow.webhook_token_hash == token_hash)
        ).first()
        if row is None:
            return None
        return _row_to_watch(row)


def list_active_watches() -> list[object]:
    from sqlmodel import select

    from app.db import cache_session
    from app.models.flights import FlightWatchRow

    with cache_session() as session:
        rows = session.exec(select(FlightWatchRow).where(FlightWatchRow.status == "ACTIVE")).all()
        return [_row_to_watch(r) for r in rows]


def list_watches_for_user(user_id: str, include_terminal: bool = False) -> list[object]:
    from sqlmodel import select

    from app.db import cache_session
    from app.models.flights import FlightWatchRow

    with cache_session() as session:
        q = select(FlightWatchRow).where(FlightWatchRow.user_id == user_id)
        if not include_terminal:
            q = q.where(FlightWatchRow.status == "ACTIVE")
        rows = session.exec(q).all()
        return [_row_to_watch(r) for r in rows]


def list_active_watches_pending_subscription(max_departure_days: int) -> list[object]:
    """Return ACTIVE watches with no alert subscription and departure within max_departure_days."""
    from datetime import timedelta

    from sqlmodel import select

    from app.db import cache_session
    from app.models.flights import FlightWatchRow

    cutoff = (datetime.now(timezone.utc) + timedelta(days=max_departure_days)).date()
    with cache_session() as session:
        rows = session.exec(
            select(FlightWatchRow)
            .where(FlightWatchRow.status == "ACTIVE")
            .where(FlightWatchRow.provider_alert_id.is_(None))  # type: ignore[union-attr]
            .where(FlightWatchRow.scheduled_departure_date <= cutoff)
        ).all()
        return [_row_to_watch(r) for r in rows]


def _row_to_watch(row: object) -> object:
    from app.flights.models import FlightWatch
    from app.models.flights import FlightWatchRow

    assert isinstance(row, FlightWatchRow)
    try:
        codeshares = json.loads(row.codeshares_json or "[]")
    except Exception:
        codeshares = []
    try:
        policy = json.loads(row.notify_policy_json or "{}")
    except Exception:
        policy = {}

    return FlightWatch(
        id=row.id,
        household_id=row.household_id,
        user_id=row.user_id,
        channel_user_id=row.channel_user_id,
        label=row.label,
        carrier_code=row.carrier_code,
        flight_number=row.flight_number,
        scheduled_departure_date=row.scheduled_departure_date,
        origin=row.origin,
        destination=row.destination,
        operating_carrier_code=row.operating_carrier_code,
        marketing_carrier_code=row.marketing_carrier_code,
        codeshares=codeshares,
        aircraft_type=row.aircraft_type,
        tail_number=row.tail_number,
        status=row.status,
        status_reason=row.status_reason,
        monitoring_starts_at=_utc(row.monitoring_starts_at),
        monitoring_ends_at=_utc(row.monitoring_ends_at),
        provider=row.provider,
        provider_flight_id=row.provider_flight_id,
        provider_alert_id=row.provider_alert_id,
        provider_subscription_kind=row.provider_subscription_kind,
        webhook_token_hash=row.webhook_token_hash,
        consecutive_provider_errors=row.consecutive_provider_errors,
        notify_policy=policy,
        created_at=_utc(row.created_at),
        updated_at=_utc(row.updated_at),
        completed_at=_utc(row.completed_at),
    )


# ---------------------------------------------------------------------------
# FlightStatusSnapshot
# ---------------------------------------------------------------------------


def save_snapshot(snapshot: object) -> None:
    from app.db import cache_session
    from app.flights.models import FlightStatusSnapshot
    from app.models.flights import FlightStatusSnapshotRow

    assert isinstance(snapshot, FlightStatusSnapshot)

    with cache_session() as session:
        row = session.get(FlightStatusSnapshotRow, snapshot.id)
        if row is None:
            row = FlightStatusSnapshotRow(id=snapshot.id)
        row.watch_id = snapshot.watch_id
        row.provider = snapshot.provider
        row.provider_updated_at = snapshot.provider_updated_at
        row.fetched_at = snapshot.fetched_at
        row.state = snapshot.state
        row.scheduled_out = snapshot.scheduled_out
        row.estimated_out = snapshot.estimated_out
        row.actual_out = snapshot.actual_out
        row.scheduled_off = snapshot.scheduled_off
        row.estimated_off = snapshot.estimated_off
        row.actual_off = snapshot.actual_off
        row.scheduled_on = snapshot.scheduled_on
        row.estimated_on = snapshot.estimated_on
        row.actual_on = snapshot.actual_on
        row.scheduled_in = snapshot.scheduled_in
        row.estimated_in = snapshot.estimated_in
        row.actual_in = snapshot.actual_in
        row.departure_terminal = snapshot.departure_terminal
        row.departure_gate = snapshot.departure_gate
        row.arrival_terminal = snapshot.arrival_terminal
        row.arrival_gate = snapshot.arrival_gate
        row.baggage_claim = snapshot.baggage_claim
        row.delay_minutes = snapshot.delay_minutes
        row.cancelled = snapshot.cancelled
        row.diverted = snapshot.diverted
        row.diversion_airport = snapshot.diversion_airport
        row.raw_json = snapshot.raw_json
        row.fetch_source = snapshot.fetch_source
        session.add(row)
        session.commit()


def get_latest_snapshot(watch_id: str) -> object | None:
    from sqlmodel import col, select

    from app.db import cache_session
    from app.models.flights import FlightStatusSnapshotRow

    with cache_session() as session:
        row = session.exec(
            select(FlightStatusSnapshotRow)
            .where(FlightStatusSnapshotRow.watch_id == watch_id)
            .order_by(col(FlightStatusSnapshotRow.fetched_at).desc())
            .limit(1)
        ).first()
        if row is None:
            return None
        return _row_to_snapshot(row)


def _utc(dt: object) -> datetime | None:
    """Ensure a datetime read from SQLite is timezone-aware (UTC)."""
    if isinstance(dt, datetime) and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    if isinstance(dt, datetime):
        return dt
    return None


def _row_to_snapshot(row: object) -> object:
    from app.flights.models import FlightStatusSnapshot
    from app.models.flights import FlightStatusSnapshotRow

    assert isinstance(row, FlightStatusSnapshotRow)
    fetched_at = _utc(row.fetched_at) or datetime.now(timezone.utc)
    return FlightStatusSnapshot(
        id=row.id,
        watch_id=row.watch_id,
        provider=row.provider,
        provider_updated_at=_utc(row.provider_updated_at),
        fetched_at=fetched_at,
        state=row.state,
        scheduled_out=_utc(row.scheduled_out),
        estimated_out=_utc(row.estimated_out),
        actual_out=_utc(row.actual_out),
        scheduled_off=_utc(row.scheduled_off),
        estimated_off=_utc(row.estimated_off),
        actual_off=_utc(row.actual_off),
        scheduled_on=_utc(row.scheduled_on),
        estimated_on=_utc(row.estimated_on),
        actual_on=_utc(row.actual_on),
        scheduled_in=_utc(row.scheduled_in),
        estimated_in=_utc(row.estimated_in),
        actual_in=_utc(row.actual_in),
        departure_terminal=row.departure_terminal,
        departure_gate=row.departure_gate,
        arrival_terminal=row.arrival_terminal,
        arrival_gate=row.arrival_gate,
        baggage_claim=row.baggage_claim,
        delay_minutes=row.delay_minutes,
        cancelled=row.cancelled,
        diverted=row.diverted,
        diversion_airport=row.diversion_airport,
        raw_json=row.raw_json,
        fetch_source=row.fetch_source if hasattr(row, "fetch_source") else "poll",
    )


# ---------------------------------------------------------------------------
# FlightEvent
# ---------------------------------------------------------------------------


def save_event(event: object) -> None:
    from app.db import cache_session
    from app.flights.models import FlightEvent
    from app.models.flights import FlightEventRow

    assert isinstance(event, FlightEvent)

    with cache_session() as session:
        row = session.get(FlightEventRow, event.id)
        if row is None:
            row = FlightEventRow(id=event.id)
        row.watch_id = event.watch_id
        row.provider = event.provider
        row.provider_event_id = event.provider_event_id
        row.event_hash = event.event_hash
        row.event_type = event.event_type
        row.severity = event.severity
        row.received_at = event.received_at
        row.provider_timestamp = event.provider_timestamp
        row.raw_json = event.raw_json
        row.normalized_json = event.normalized_json
        row.processed = event.processed
        session.add(row)
        session.commit()


def list_snapshots_for_watch(watch_id: str, limit: int = 20) -> list[object]:
    from sqlmodel import col, select

    from app.db import cache_session
    from app.models.flights import FlightStatusSnapshotRow

    with cache_session() as session:
        rows = session.exec(
            select(FlightStatusSnapshotRow)
            .where(FlightStatusSnapshotRow.watch_id == watch_id)
            .order_by(col(FlightStatusSnapshotRow.fetched_at).asc())
            .limit(limit)
        ).all()
        return [_row_to_snapshot(r) for r in rows]


def list_events_for_watch(watch_id: str, limit: int = 30) -> list[object]:
    from sqlmodel import col, select

    from app.db import cache_session
    from app.models.flights import FlightEventRow

    with cache_session() as session:
        rows = session.exec(
            select(FlightEventRow)
            .where(FlightEventRow.watch_id == watch_id)
            .order_by(col(FlightEventRow.received_at).asc())
            .limit(limit)
        ).all()
        return list(rows)


def event_hash_exists(event_hash: str) -> bool:
    from sqlmodel import select

    from app.db import cache_session
    from app.models.flights import FlightEventRow

    with cache_session() as session:
        row = session.exec(
            select(FlightEventRow).where(FlightEventRow.event_hash == event_hash).limit(1)
        ).first()
        return row is not None


# ---------------------------------------------------------------------------
# Retention cleanup
# ---------------------------------------------------------------------------


def delete_old_events(retention_days: int) -> int:
    from datetime import timedelta

    from sqlmodel import delete as sql_delete

    from app.db import cache_session
    from app.models.flights import FlightEventRow

    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    with cache_session() as session:
        from sqlmodel import col as _col

        result = session.exec(
            sql_delete(FlightEventRow).where(_col(FlightEventRow.received_at) < cutoff)
        )
        session.commit()
        return result.rowcount


def delete_old_terminal_watches(retention_days: int) -> int:
    from datetime import timedelta

    from sqlmodel import col, select
    from sqlmodel import delete as sql_delete

    from app.db import cache_session
    from app.models.flights import FlightStatusSnapshotRow, FlightWatchRow

    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    terminal = ("COMPLETED", "CANCELLED", "FAILED")
    with cache_session() as session:
        old_watches = session.exec(
            select(FlightWatchRow)
            .where(col(FlightWatchRow.status).in_(terminal))
            .where(col(FlightWatchRow.completed_at) < cutoff)
        ).all()
        ids = [w.id for w in old_watches]
        if not ids:
            return 0
        session.exec(
            sql_delete(FlightStatusSnapshotRow).where(
                col(FlightStatusSnapshotRow.watch_id).in_(ids)
            )
        )
        session.exec(sql_delete(FlightWatchRow).where(col(FlightWatchRow.id).in_(ids)))
        session.commit()
        return len(ids)
