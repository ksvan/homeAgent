from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

# ---------------------------------------------------------------------------
# Normalized flight states — one internal vocabulary across all providers
# ---------------------------------------------------------------------------

FLIGHT_STATES = {
    "SCHEDULED",
    "DELAYED",
    "CHECK_IN_OPEN",
    "BOARDING",
    "OUT_GATE",
    "IN_AIR",
    "LANDED",
    "IN_GATE",
    "CANCELLED",
    "DIVERTED",
    "UNKNOWN",
}

TERMINAL_STATES = {"IN_GATE", "CANCELLED", "DIVERTED"}

# Watch lifecycle statuses
WATCH_ACTIVE = "ACTIVE"
WATCH_COMPLETED = "COMPLETED"
WATCH_CANCELLED = "CANCELLED"
WATCH_FAILED = "FAILED"


@dataclass
class FlightWatch:
    id: str
    household_id: str
    user_id: str
    channel_user_id: str

    carrier_code: str
    flight_number: str
    scheduled_departure_date: date

    provider: str
    status: str  # ACTIVE | COMPLETED | CANCELLED | FAILED

    label: str | None = None
    origin: str | None = None
    destination: str | None = None

    operating_carrier_code: str | None = None
    marketing_carrier_code: str | None = None
    codeshares: list[str] = field(default_factory=list)
    aircraft_type: str | None = None
    tail_number: str | None = None

    status_reason: str | None = None
    monitoring_starts_at: datetime | None = None
    monitoring_ends_at: datetime | None = None

    provider_flight_id: str | None = None
    provider_alert_id: str | None = None
    provider_subscription_kind: str | None = None
    webhook_token_hash: str | None = None

    consecutive_provider_errors: int = 0
    notify_policy: dict[str, Any] = field(default_factory=dict)

    created_at: datetime | None = None
    updated_at: datetime | None = None
    completed_at: datetime | None = None

    @property
    def flight_label(self) -> str:
        return (
            self.label
            or f"{self.carrier_code}{self.flight_number} {self.scheduled_departure_date}"
        )

    @property
    def is_terminal(self) -> bool:
        return self.status in (WATCH_COMPLETED, WATCH_CANCELLED, WATCH_FAILED)

    @property
    def has_alert_subscription(self) -> bool:
        return self.provider_alert_id is not None


@dataclass
class FlightStatusSnapshot:
    id: str
    watch_id: str
    provider: str
    fetched_at: datetime

    state: str = "UNKNOWN"

    scheduled_out: datetime | None = None
    estimated_out: datetime | None = None
    actual_out: datetime | None = None
    scheduled_off: datetime | None = None
    estimated_off: datetime | None = None
    actual_off: datetime | None = None
    scheduled_on: datetime | None = None
    estimated_on: datetime | None = None
    actual_on: datetime | None = None
    scheduled_in: datetime | None = None
    estimated_in: datetime | None = None
    actual_in: datetime | None = None

    departure_terminal: str | None = None
    departure_gate: str | None = None
    arrival_terminal: str | None = None
    arrival_gate: str | None = None
    baggage_claim: str | None = None

    delay_minutes: int | None = None
    cancelled: bool = False
    diverted: bool = False
    diversion_airport: str | None = None

    provider_updated_at: datetime | None = None
    raw_json: str = "{}"
    fetch_source: str = "poll"  # "poll" | "webhook"

    def effective_departure(self) -> datetime | None:
        return self.actual_off or self.estimated_off or self.scheduled_off

    def effective_arrival(self) -> datetime | None:
        return self.actual_in or self.estimated_in or self.scheduled_in

    def to_summary_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "departure_gate": self.departure_gate,
            "departure_terminal": self.departure_terminal,
            "arrival_gate": self.arrival_gate,
            "arrival_terminal": self.arrival_terminal,
            "baggage_claim": self.baggage_claim,
            "delay_minutes": self.delay_minutes,
            "cancelled": self.cancelled,
            "diverted": self.diverted,
            "diversion_airport": self.diversion_airport,
            "estimated_departure": self.estimated_off.isoformat() if self.estimated_off else None,
            "actual_departure": self.actual_off.isoformat() if self.actual_off else None,
            "estimated_arrival": self.estimated_in.isoformat() if self.estimated_in else None,
            "actual_arrival": self.actual_in.isoformat() if self.actual_in else None,
            "fetched_at": self.fetched_at.isoformat(),
            "fetch_source": self.fetch_source,
        }


@dataclass
class FlightEvent:
    id: str
    provider: str
    event_hash: str
    event_type: str
    received_at: datetime

    watch_id: str | None = None
    provider_event_id: str | None = None
    severity: str = "info"
    provider_timestamp: datetime | None = None
    raw_json: str = "{}"
    normalized_json: str = "{}"
    processed: bool = False


@dataclass
class FlightStatusChange:
    """A meaningful diff between two snapshots, ready to drive notification decisions."""

    watch_id: str
    flight_label: str
    change_type: str  # e.g. "gate_changed", "delay_increased", "cancelled", etc.
    severity: str     # critical | warning | info | debug
    summary: str
    old_values: dict[str, Any] = field(default_factory=dict)
    new_values: dict[str, Any] = field(default_factory=dict)

    def to_context_block(self) -> str:
        lines = [
            "## Flight Monitor Event",
            f"- flight: {self.flight_label}",
            f"- change: {self.change_type}",
            f"- severity: {self.severity}",
            f"- summary: {self.summary}",
        ]
        if self.old_values:
            lines.append(f"- previous: {json.dumps(self.old_values)}")
        if self.new_values:
            lines.append(f"- current: {json.dumps(self.new_values)}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Default notification policy
# ---------------------------------------------------------------------------

DEFAULT_NOTIFY_POLICY: dict[str, Any] = {
    "delay_threshold_minutes": 15,
    "notify_gate_changes": True,
    "notify_terminal_changes": True,
    "notify_cancellations": True,
    "notify_diversions": True,
    "notify_boarding": True,
    "notify_aircraft_changes": False,
    "notify_inbound_aircraft_arrived": True,
    "notify_minor_time_changes": False,
    "quiet_hours_mode": "urgent_only",
}
