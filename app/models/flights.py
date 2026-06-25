from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Optional

from sqlmodel import Field, SQLModel


def _now() -> datetime:
    return datetime.now(timezone.utc)


class FlightWatchRow(SQLModel, table=True):
    """Persisted user intent to monitor a specific flight leg."""

    __tablename__ = "flightwatch"

    id: str = Field(primary_key=True)
    household_id: str
    user_id: str
    channel_user_id: str

    label: Optional[str] = None
    carrier_code: str
    flight_number: str
    scheduled_departure_date: date
    origin: Optional[str] = None
    destination: Optional[str] = None

    # Segment details resolved from first provider lookup
    operating_carrier_code: Optional[str] = None
    marketing_carrier_code: Optional[str] = None
    codeshares_json: str = "[]"
    aircraft_type: Optional[str] = None
    tail_number: Optional[str] = None

    status: str = "ACTIVE"  # ACTIVE | COMPLETED | CANCELLED | FAILED
    status_reason: Optional[str] = None
    monitoring_starts_at: Optional[datetime] = None
    monitoring_ends_at: Optional[datetime] = None

    provider: str
    provider_flight_id: Optional[str] = None
    provider_alert_id: Optional[str] = None
    provider_subscription_kind: Optional[str] = None
    webhook_token_hash: Optional[str] = None

    consecutive_provider_errors: int = 0
    notify_policy_json: str = "{}"

    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)
    completed_at: Optional[datetime] = None


class FlightStatusSnapshotRow(SQLModel, table=True):
    """Latest normalized operational status for a flight watch."""

    __tablename__ = "flightstatussnapshot"

    id: str = Field(primary_key=True)
    watch_id: str = Field(index=True)
    provider: str
    provider_updated_at: Optional[datetime] = None
    fetched_at: datetime = Field(default_factory=_now)

    state: str = "UNKNOWN"

    scheduled_out: Optional[datetime] = None
    estimated_out: Optional[datetime] = None
    actual_out: Optional[datetime] = None
    scheduled_off: Optional[datetime] = None
    estimated_off: Optional[datetime] = None
    actual_off: Optional[datetime] = None
    scheduled_on: Optional[datetime] = None
    estimated_on: Optional[datetime] = None
    actual_on: Optional[datetime] = None
    scheduled_in: Optional[datetime] = None
    estimated_in: Optional[datetime] = None
    actual_in: Optional[datetime] = None

    departure_terminal: Optional[str] = None
    departure_gate: Optional[str] = None
    arrival_terminal: Optional[str] = None
    arrival_gate: Optional[str] = None
    baggage_claim: Optional[str] = None

    delay_minutes: Optional[int] = None
    cancelled: bool = False
    diverted: bool = False
    diversion_airport: Optional[str] = None
    raw_json: str = "{}"
    fetch_source: str = "poll"  # "poll" | "webhook"


class FlightEventRow(SQLModel, table=True):
    """Raw and normalized incoming vendor event for a flight watch."""

    __tablename__ = "flightevent"

    id: str = Field(primary_key=True)
    watch_id: Optional[str] = Field(default=None, index=True)
    provider: str
    provider_event_id: Optional[str] = None
    event_hash: str = Field(index=True)
    event_type: str
    severity: str = "info"
    received_at: datetime = Field(default_factory=_now)
    provider_timestamp: Optional[datetime] = None
    raw_json: str = "{}"
    normalized_json: str = "{}"
    processed: bool = False
