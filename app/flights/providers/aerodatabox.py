from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from app.flights.providers.base import (
    AlertCreditBalance,
    FlightProvider,
    FlightQuery,
    ProviderAlert,
    ProviderAlertDeferredError,
    ProviderError,
    ProviderFlightNotFoundError,
    ProviderQuotaError,
    ResolvedFlight,
)

if TYPE_CHECKING:
    from app.flights.models import FlightStatusSnapshot, FlightWatch

logger = logging.getLogger(__name__)

# Normalized state mapping from AeroDataBox flight states
_STATE_MAP: dict[str, str] = {
    "Unknown": "UNKNOWN",
    "Expected": "SCHEDULED",
    "Scheduled": "SCHEDULED",
    "Delayed": "DELAYED",
    "Departed": "OUT_GATE",
    "InFlight": "IN_AIR",
    "Landed": "LANDED",
    "Arrived": "IN_GATE",
    "Canceled": "CANCELLED",
    "Cancelled": "CANCELLED",
    "Diverted": "DIVERTED",
    "GateClosed": "BOARDING",
    "Boarding": "BOARDING",
    "CheckIn": "CHECK_IN_OPEN",
}


class AeroDataBoxProvider(FlightProvider):
    name = "aerodatabox"

    supports_webhooks = True
    supports_gate_changes = True
    supports_terminal_changes = True
    supports_baggage = True
    supports_track_positions = False
    supports_codeshares = True
    alert_lead_time_days = 2  # to be verified in Phase 0 spike

    def __init__(
        self,
        rapidapi_key: str,
        rapidapi_host: str,
        base_url: str,
        alerts_enabled: bool = True,
    ) -> None:
        self._key = rapidapi_key
        self._host = rapidapi_host
        self._base_url = base_url.rstrip("/")
        self._alerts_enabled = alerts_enabled

    def _headers(self) -> dict[str, str]:
        return {
            "X-RapidAPI-Key": self._key,
            "X-RapidAPI-Host": self._host,
            "Accept": "application/json",
        }

    async def resolve_flight(self, query: FlightQuery) -> list[ResolvedFlight]:
        """Look up a flight by number and date to get provider IDs and segment details."""
        import httpx

        date_str = query.departure_date.strftime("%Y-%m-%d")
        url = (
            f"{self._base_url}/flights/number/{query.carrier_code}{query.flight_number}/{date_str}"
        )
        params: dict[str, str] = {
            "withAircraftImage": "false",
            "withLocation": "false",
            "withFlightPlan": "false",
            "dateLocalRole": "Both",
        }

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(url, headers=self._headers(), params=params)

        if resp.status_code in (204, 404):
            raise ProviderFlightNotFoundError(
                f"Flight {query.carrier_code}{query.flight_number}"
                f" on {query.departure_date} not found"
            )
        if resp.status_code == 429:
            raise ProviderQuotaError("AeroDataBox quota exceeded")
        if resp.status_code >= 400:
            raise ProviderError(f"AeroDataBox resolve_flight {resp.status_code}: {resp.text[:200]}")

        data = resp.json()
        # AeroDataBox returns a dict with "departures"/"arrivals" or a list of flights
        flights = _extract_flights(data)
        if not flights:
            raise ProviderFlightNotFoundError(
                f"No matching flights returned for {query.carrier_code}{query.flight_number}"
            )

        return [_parse_resolved_flight(f) for f in flights]

    async def get_status(
        self,
        provider_flight_id: str | None,
        query: FlightQuery,
    ) -> "FlightStatusSnapshot":
        """Fetch current status. Uses provider_flight_id when available for precision."""

        import httpx

        date_str = query.departure_date.strftime("%Y-%m-%d")
        url = (
            f"{self._base_url}/flights/number/{query.carrier_code}{query.flight_number}/{date_str}"
        )
        params: dict[str, str] = {
            "withAircraftImage": "false",
            "withLocation": "false",
            "withFlightPlan": "false",
            "dateLocalRole": "Both",
        }

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(url, headers=self._headers(), params=params)

        if resp.status_code in (204, 404):
            raise ProviderFlightNotFoundError(
                f"Flight {query.carrier_code}{query.flight_number} not found"
            )
        if resp.status_code == 429:
            raise ProviderQuotaError("AeroDataBox quota exceeded")
        if resp.status_code >= 400:
            raise ProviderError(f"AeroDataBox get_status {resp.status_code}: {resp.text[:200]}")

        data = resp.json()
        flights = _extract_flights(data)
        if not flights:
            raise ProviderFlightNotFoundError("No flights in status response")

        # Pick the best matching flight
        flight_data = _pick_best(flights, provider_flight_id, query)
        snapshot_id = hashlib.sha1(
            f"{query.carrier_code}{query.flight_number}:{query.departure_date}:{datetime.now(timezone.utc).isoformat()}".encode()
        ).hexdigest()[:24]

        return _parse_snapshot(
            snapshot_id=snapshot_id,
            watch_id="",  # filled in by service layer
            flight_data=flight_data,
            raw=data,
        )

    async def create_alert(
        self,
        watch: "FlightWatch",
        webhook_url: str,
    ) -> ProviderAlert:
        """Subscribe to push alerts for a flight number via webhook."""
        import httpx

        if not self._alerts_enabled:
            raise ProviderError("AeroDataBox alerts are not enabled")

        flight_id = f"{watch.carrier_code}{watch.flight_number}"
        url = f"{self._base_url}/subscriptions/webhook/FlightByNumber/{flight_id}"
        body = {"url": webhook_url}
        params = {"useCredits": "true"}

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(url, headers=self._headers(), json=body, params=params)

        if resp.status_code == 429:
            raise ProviderQuotaError("AeroDataBox quota exceeded when creating alert")

        if resp.status_code in (400, 422):
            text = resp.text.lower()
            if "lead" in text or "advance" in text or "too early" in text or "future" in text:
                raise ProviderAlertDeferredError(
                    f"Flight {flight_id} is too far out for alert subscription"
                )
            raise ProviderError(f"AeroDataBox create_alert {resp.status_code}: {resp.text[:200]}")

        if resp.status_code >= 400:
            raise ProviderError(f"AeroDataBox create_alert {resp.status_code}: {resp.text[:200]}")

        data = resp.json()
        alert_id = str(data.get("id") or "")
        if not alert_id:
            raise ProviderError(f"AeroDataBox create_alert returned no ID: {data}")

        return ProviderAlert(
            alert_id=alert_id,
            subscription_kind="flight_number",
            webhook_url=webhook_url,
        )

    async def delete_alert(self, provider_alert_id: str) -> None:
        import httpx

        url = f"{self._base_url}/subscriptions/{provider_alert_id}"
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.delete(url, headers=self._headers())

        if resp.status_code == 404:
            logger.warning(
                "AeroDataBox alert %s not found on delete — already removed", provider_alert_id
            )
            return
        if resp.status_code >= 400:
            raise ProviderError(f"AeroDataBox delete_alert {resp.status_code}: {resp.text[:200]}")

    async def get_alert_credit_balance(self) -> AlertCreditBalance:
        """Fetch remaining alert credits from AeroDataBox.

        Phase 0 note: verify the exact balance endpoint from live API docs.
        """
        import httpx

        from app.config import get_settings

        url = f"{self._base_url}/subscriptions/balance"
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url, headers=self._headers())

        if resp.status_code >= 400:
            raise ProviderError(f"AeroDataBox balance check {resp.status_code}: {resp.text[:200]}")

        try:
            data = resp.json()
        except Exception:
            data = {}
        remaining = int(data.get("creditsRemaining") or 0)
        total = data.get("total")
        settings = get_settings()
        threshold = settings.flight_alert_min_credits

        return AlertCreditBalance(
            remaining=remaining,
            total=int(total) if total is not None else None,
            low=remaining <= threshold,
            empty=remaining == 0,
        )

    def verify_webhook(
        self,
        headers: dict[str, str],
        body: bytes,
        webhook_token: str,
    ) -> bool:
        # AeroDataBox does not sign webhook requests in the documented API.
        # The watch-scoped URL token is the security boundary.
        return True

    def normalize_webhook(self, body: bytes) -> dict[str, Any]:
        """Parse AeroDataBox webhook body to a normalized event dict.

        Phase 0 note: update field mapping after examining real payloads.
        """
        try:
            data = json.loads(body)
        except Exception as exc:
            raise ProviderError(f"Invalid JSON in webhook body: {exc}") from exc

        return {
            "provider": self.name,
            "provider_event_id": data.get("id") or data.get("eventId"),
            "event_type": data.get("eventType") or data.get("type") or "status_update",
            "raw": data,
        }


# ---------------------------------------------------------------------------
# Internal helpers — adapt as actual AeroDataBox response shape becomes known
# ---------------------------------------------------------------------------


def _extract_flights(data: Any) -> list[dict[str, Any]]:
    """Extract the list of flight objects from whatever AeroDataBox returns."""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("flights", "departures", "arrivals", "items", "data"):
            if key in data and isinstance(data[key], list):
                return list(data[key])
        # Single flight dict
        if "number" in data or "flightNumber" in data or "iata" in data:
            return [data]
    return []


def _pick_best(
    flights: list[dict[str, Any]],
    provider_flight_id: str | None,
    query: FlightQuery,
) -> dict[str, Any]:
    if provider_flight_id:
        for f in flights:
            if str(f.get("id") or "") == provider_flight_id:
                return f
    return flights[0]


def _parse_resolved_flight(f: dict[str, Any]) -> ResolvedFlight:
    carrier = f.get("airline") or {}
    iata = carrier.get("iata") or f.get("iata") or f.get("carrierCode") or ""

    # "number" field is the full IATA flight number e.g. "DY 4104" — strip carrier prefix
    number_raw = f.get("flightNumber") or f.get("number") or f.get("iataNumber") or ""
    if iata and number_raw.startswith(iata):
        number = number_raw[len(iata) :].strip()
    else:
        number = number_raw.strip()

    dep = f.get("departure") or {}
    arr = f.get("arrival") or {}

    # scheduledTime is a nested object: {"utc": "...", "local": "..."}
    dep_sched = dep.get("scheduledTime") or {}
    dep_date_raw = dep_sched.get("local") or dep_sched.get("utc") or ""
    try:
        from datetime import date as _date

        dep_date = _date.fromisoformat(dep_date_raw[:10]) if dep_date_raw else _date.today()
    except Exception:
        from datetime import date as _date

        dep_date = _date.today()

    return ResolvedFlight(
        provider_flight_id=str(f.get("id") or f.get("flightId") or f"{iata}{number}"),
        carrier_code=iata,
        flight_number=number,
        departure_date=dep_date,
        origin=(
            dep.get("airport", {}).get("iata") if isinstance(dep.get("airport"), dict) else None
        ),
        destination=(
            arr.get("airport", {}).get("iata") if isinstance(arr.get("airport"), dict) else None
        ),
        operating_carrier_code=(f.get("operatingAirline") or {}).get("iata"),
        codeshares=[c.get("iata", "") for c in (f.get("codeshares") or []) if c.get("iata")],
        aircraft_type=(f.get("aircraft") or {}).get("model"),
        tail_number=(f.get("aircraft") or {}).get("reg"),
    )


def _parse_snapshot(
    snapshot_id: str,
    watch_id: str,
    flight_data: dict[str, Any],
    raw: Any,
) -> "FlightStatusSnapshot":
    from app.flights.models import FlightStatusSnapshot

    dep = flight_data.get("departure") or {}
    arr = flight_data.get("arrival") or {}

    def _dt(val: str | None) -> datetime | None:
        if not val:
            return None
        try:
            return datetime.fromisoformat(val.replace("Z", "+00:00"))
        except Exception:
            return None

    status_raw = flight_data.get("status") or flight_data.get("flightStatus") or "Unknown"
    state = _STATE_MAP.get(status_raw, "UNKNOWN")

    def _utc(block: dict[str, Any] | None, key: str = "utc") -> str | None:
        """Extract UTC string from a nested time block like {"utc": "...", "local": "..."}."""
        return (block or {}).get(key)

    # Compute delay from scheduled vs revised departure (API doesn't expose it directly)
    sched_off_str = _utc(dep.get("scheduledTime"))
    revised_off_str = _utc(dep.get("revisedTime"))
    delay: int | None = None
    if sched_off_str and revised_off_str:
        try:
            sched_dt = datetime.fromisoformat(sched_off_str.replace("Z", "+00:00"))
            rev_dt = datetime.fromisoformat(revised_off_str.replace("Z", "+00:00"))
            diff = int((rev_dt - sched_dt).total_seconds() / 60)
            delay = diff if diff > 0 else None
        except Exception:
            pass

    return FlightStatusSnapshot(
        id=snapshot_id,
        watch_id=watch_id,
        provider="aerodatabox",
        fetched_at=datetime.now(timezone.utc),
        state=state,
        scheduled_off=_dt(_utc(dep.get("scheduledTime"))),
        estimated_off=_dt(_utc(dep.get("revisedTime")) or _utc(dep.get("predictedTime"))),
        actual_off=_dt(_utc(dep.get("actualTime"))),
        scheduled_in=_dt(_utc(arr.get("scheduledTime"))),
        estimated_in=_dt(_utc(arr.get("revisedTime")) or _utc(arr.get("predictedTime"))),
        actual_in=_dt(_utc(arr.get("actualTime"))),
        departure_terminal=dep.get("terminal"),
        departure_gate=dep.get("gate"),
        arrival_terminal=arr.get("terminal"),
        arrival_gate=arr.get("gate"),
        baggage_claim=arr.get("baggageBelt"),
        delay_minutes=delay,
        cancelled=state == "CANCELLED",
        diverted=state == "DIVERTED",
        raw_json=json.dumps(raw, default=str),
    )
