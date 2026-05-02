from __future__ import annotations

import json
import logging
from typing import Optional

from pydantic_ai import Agent, RunContext

from app.agent.agent import AgentDeps

logger = logging.getLogger(__name__)


def register_flight_tools(agent: Agent[AgentDeps, str]) -> None:
    """Attach flight monitoring tools to the conversation agent."""

    @agent.tool
    async def track_flight(
        ctx: RunContext[AgentDeps],
        carrier_code: str,
        flight_number: str,
        departure_date: str,
        origin: Optional[str] = None,
        destination: Optional[str] = None,
        label: Optional[str] = None,
    ) -> str:
        """Start monitoring a flight so HomeAgent tracks its status and notifies on changes.

        Use when the user says they want to track, follow, or be notified about a flight.

        Args:
            carrier_code: IATA carrier code, e.g. "SK" for SAS, "DY" for Norwegian.
            flight_number: Flight number without carrier prefix, e.g. "1461".
            departure_date: Departure date in YYYY-MM-DD format.
            origin: IATA airport code of departure airport, e.g. "OSL". Improves matching.
            destination: IATA airport code of arrival airport, e.g. "CPH". Optional.
            label: Optional user-facing label, e.g. "Monday to Stockholm".
        """
        from app.flights.service import track_flight as _track_flight

        result = await _track_flight(
            user_id=ctx.deps.user_id,
            household_id=ctx.deps.household_id,
            channel_user_id=ctx.deps.channel_user_id,
            carrier_code=carrier_code,
            flight_number=flight_number,
            departure_date_str=departure_date,
            origin=origin,
            destination=destination,
            label=label,
        )

        if not result.get("ok"):
            error = result.get("error", "Unknown error")
            if "candidates" in result:
                return (
                    f"{error}\n\nMatching flights:\n"
                    + json.dumps(result["candidates"], indent=2)
                )
            return f"Could not start monitoring: {error}"

        note = result.get("alert_deferred_note")
        status = result.get("initial_status") or {}
        state = status.get("state", "UNKNOWN")
        delay = status.get("delay_minutes")
        gate = status.get("departure_gate")

        parts = [f"Now monitoring {result['flight']} (watch ID: {result['watch_id']})."]
        if state not in ("UNKNOWN", "SCHEDULED", ""):
            parts.append(f"Current status: {state}.")
        if delay:
            parts.append(f"Current delay: {delay} minutes.")
        if gate:
            parts.append(f"Departure gate: {gate}.")
        if note:
            parts.append(note)

        return " ".join(parts)

    @agent.tool
    async def get_flight_status(
        ctx: RunContext[AgentDeps],
        flight_watch_id: Optional[str] = None,
        carrier_code: Optional[str] = None,
        flight_number: Optional[str] = None,
        departure_date: Optional[str] = None,
    ) -> str:
        """Get the current status of a tracked or untracked flight.

        Use for questions like "how is my flight looking?", "what gate is SK1461?",
        "is my flight tonight on time?"

        If the user has one active watch, it is used automatically.
        For an untracked flight, provide carrier_code + flight_number + departure_date.

        Args:
            flight_watch_id: Watch ID from a previous track_flight call.
            carrier_code: IATA carrier code (for ad hoc lookups).
            flight_number: Flight number without carrier prefix (for ad hoc lookups).
            departure_date: Departure date in YYYY-MM-DD (for ad hoc lookups).
        """
        from app.flights.service import get_flight_status as _get_flight_status

        result = await _get_flight_status(
            user_id=ctx.deps.user_id,
            watch_id=flight_watch_id,
            carrier_code=carrier_code,
            flight_number=flight_number,
            departure_date_str=departure_date,
        )

        if not result.get("ok"):
            error = result.get("error", "Unknown error")
            if "watches" in result:
                watches_str = ", ".join(
                    f"{w['flight']} (ID: {w['watch_id']})"
                    for w in result["watches"]
                )
                return f"{error} Active watches: {watches_str}"
            return error

        status = result.get("status") or {}
        stale_note = status.pop("stale_note", None)
        ad_hoc = result.get("ad_hoc", False)
        flight_label = result.get("flight", "")

        output: dict[str, object] = {}
        if not ad_hoc and flight_label:
            output["flight"] = flight_label
        if result.get("watch_id"):
            output["watch_id"] = result["watch_id"]
        output.update(status)

        result_str = json.dumps(output, default=str)
        if stale_note:
            result_str += f"\n\nNote: {stale_note}"
        return result_str

    @agent.tool
    async def list_tracked_flights(ctx: RunContext[AgentDeps]) -> str:
        """List all active flight watches for the current user.

        Use when the user asks "what flights are you tracking for me?" or
        "show me my tracked flights".
        """
        from app.flights.models import FlightWatch
        from app.flights.repository import list_watches_for_user

        watches = list_watches_for_user(ctx.deps.user_id, include_terminal=False)
        if not watches:
            return "No active flight watches found."

        result = []
        for w in watches:
            if not isinstance(w, FlightWatch):
                continue
            result.append({
                "watch_id": w.id,
                "flight": w.flight_label,
                "carrier": w.carrier_code,
                "flight_number": w.flight_number,
                "departure_date": str(w.scheduled_departure_date),
                "origin": w.origin,
                "destination": w.destination,
                "status": w.status,
                "alert_subscription": w.provider_alert_id is not None,
            })

        return json.dumps(result, default=str)

    @agent.tool
    async def cancel_flight_watch(
        ctx: RunContext[AgentDeps],
        flight_watch_id: str,
    ) -> str:
        """Stop monitoring a flight and cancel the alert subscription.

        Use when the user says "stop tracking", "cancel", or "remove" a flight watch.

        Args:
            flight_watch_id: Watch ID from list_tracked_flights or track_flight.
        """
        from app.flights.service import cancel_flight_watch as _cancel

        result = await _cancel(
            user_id=ctx.deps.user_id,
            watch_id=flight_watch_id,
        )
        if not result.get("ok"):
            return result.get("error", "Could not cancel watch.")
        return result.get("message", "Watch cancelled.")
