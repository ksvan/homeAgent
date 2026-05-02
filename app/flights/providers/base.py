from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.flights.models import FlightStatusSnapshot, FlightWatch


@dataclass
class FlightQuery:
    carrier_code: str
    flight_number: str
    departure_date: date
    origin: str | None = None
    destination: str | None = None


@dataclass
class ResolvedFlight:
    provider_flight_id: str
    carrier_code: str
    flight_number: str
    departure_date: date
    origin: str | None = None
    destination: str | None = None
    operating_carrier_code: str | None = None
    marketing_carrier_code: str | None = None
    codeshares: list[str] = field(default_factory=list)
    aircraft_type: str | None = None
    tail_number: str | None = None


@dataclass
class ProviderAlert:
    alert_id: str
    subscription_kind: str  # "flight_number"
    webhook_url: str


@dataclass
class AlertCreditBalance:
    remaining: int
    total: int | None = None
    low: bool = False
    empty: bool = False


class FlightProvider(ABC):
    name: str

    # Capability flags — override in subclass
    supports_webhooks: bool = False
    supports_gate_changes: bool = False
    supports_terminal_changes: bool = False
    supports_baggage: bool = False
    supports_track_positions: bool = False
    supports_codeshares: bool = False
    alert_lead_time_days: int | None = None  # None = no known restriction

    @abstractmethod
    async def resolve_flight(self, query: FlightQuery) -> list[ResolvedFlight]:
        """Resolve a flight query to one or more candidate flights."""
        ...

    @abstractmethod
    async def get_status(
        self,
        provider_flight_id: str | None,
        query: FlightQuery,
    ) -> "FlightStatusSnapshot":
        """Fetch current normalized status for a flight."""
        ...

    @abstractmethod
    async def create_alert(
        self,
        watch: "FlightWatch",
        webhook_url: str,
    ) -> ProviderAlert:
        """Create a vendor push alert subscription for the watch.

        Raises ProviderAlertDeferredError if the flight is too far out.
        Raises ProviderError on unrecoverable failures.
        """
        ...

    @abstractmethod
    async def delete_alert(self, provider_alert_id: str) -> None:
        """Delete a vendor push alert subscription."""
        ...

    @abstractmethod
    async def get_alert_credit_balance(self) -> AlertCreditBalance:
        """Fetch current alert credit balance (AeroDataBox-specific concept)."""
        ...

    @abstractmethod
    def verify_webhook(
        self,
        headers: dict[str, str],
        body: bytes,
        webhook_token: str,
    ) -> bool:
        """Verify that an inbound webhook request is authentic."""
        ...

    @abstractmethod
    def normalize_webhook(self, body: bytes) -> dict[str, Any]:
        """Parse a raw vendor webhook body into a normalized event dict."""
        ...


# ---------------------------------------------------------------------------
# Provider error hierarchy
# ---------------------------------------------------------------------------

class ProviderError(Exception):
    """General provider API error."""


class ProviderAlertDeferredError(ProviderError):
    """Provider rejected alert subscription because the flight is too far out."""


class ProviderQuotaError(ProviderError):
    """Provider returned a quota / rate-limit error."""


class ProviderFlightNotFoundError(ProviderError):
    """Provider could not find the flight."""


class ProviderAmbiguousFlightError(ProviderError):
    """Provider returned multiple candidates that require user clarification."""

    def __init__(self, message: str, candidates: list[ResolvedFlight]):
        super().__init__(message)
        self.candidates = candidates
