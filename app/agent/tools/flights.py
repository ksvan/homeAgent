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
            error = str(result.get("error", "Unknown error"))
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
    async def get_flight_activity_log(
        ctx: RunContext[AgentDeps],
        flight_watch_id: Optional[str] = None,
    ) -> str:
        """Return a chronological activity log for a tracked flight.

        Use when you want to understand how data has arrived — when polls ran,
        when webhook events came in, what changed between fetches, and whether
        data is current.

        The log shows: snapshot fetches (with source "poll" or "webhook"), the
        flight state and key fields at each fetch, what changed since the
        previous fetch, and incoming webhook events.

        Args:
            flight_watch_id: Watch ID. If omitted and only one active watch
                exists for the user, it is used automatically.
        """
        from app.flights.diff import compute_changes
        from app.flights.models import FlightStatusSnapshot, FlightWatch
        from app.flights.repository import (
            list_events_for_watch,
            list_snapshots_for_watch,
            list_watches_for_user,
        )
        from app.models.flights import FlightEventRow

        watch_id = flight_watch_id
        if not watch_id:
            watches = list_watches_for_user(ctx.deps.user_id, include_terminal=False)
            if not watches:
                return "No active flight watches found."
            if len(watches) > 1:
                ids = ", ".join(
                    f"{w.flight_label} (ID: {w.id})"
                    for w in watches
                    if isinstance(w, FlightWatch)
                )
                return f"Multiple active watches — specify flight_watch_id. Watches: {ids}"
            w = watches[0]
            if not isinstance(w, FlightWatch):
                return "Could not resolve watch."
            watch_id = w.id

        watches = list_watches_for_user(ctx.deps.user_id, include_terminal=True)
        watch = next((w for w in watches if isinstance(w, FlightWatch) and w.id == watch_id), None)
        if watch is None:
            return f"Watch {watch_id} not found."

        snapshots = [s for s in list_snapshots_for_watch(watch_id, limit=25)
                     if isinstance(s, FlightStatusSnapshot)]
        events = [e for e in list_events_for_watch(watch_id, limit=40)
                  if isinstance(e, FlightEventRow)]

        # Build a merged timeline of entries
        timeline: list[dict[str, object]] = []

        for i, snap in enumerate(snapshots):
            prev = snapshots[i - 1] if i > 0 else None
            notify_policy = watch.notify_policy
            changes = compute_changes(watch, prev, snap, notify_policy) if prev else []

            entry: dict[str, object] = {
                "type": "fetch",
                "source": snap.fetch_source,
                "at": snap.fetched_at.isoformat(),
                "state": snap.state,
            }
            if snap.departure_gate:
                entry["gate"] = snap.departure_gate
            if snap.departure_terminal:
                entry["terminal"] = snap.departure_terminal
            if snap.delay_minutes:
                entry["delay_minutes"] = snap.delay_minutes
            if snap.cancelled:
                entry["cancelled"] = True
            if snap.diverted:
                entry["diverted"] = True
            if snap.estimated_off:
                entry["est_departure"] = snap.estimated_off.isoformat()
            if snap.actual_off:
                entry["actual_departure"] = snap.actual_off.isoformat()
            if snap.estimated_in:
                entry["est_arrival"] = snap.estimated_in.isoformat()
            if snap.actual_in:
                entry["actual_arrival"] = snap.actual_in.isoformat()
            if changes:
                entry["changes_detected"] = [
                    {"type": c.change_type, "severity": c.severity, "summary": c.summary}
                    for c in changes
                ]
            timeline.append(entry)

        for ev in events:
            timeline.append({
                "type": "webhook_event",
                "at": ev.received_at.isoformat(),
                "event_type": ev.event_type,
                "severity": ev.severity,
            })

        timeline.sort(key=lambda e: str(e["at"]))

        if not timeline:
            return f"No activity recorded yet for watch {watch_id}."

        summary: dict[str, object] = {
            "watch_id": watch_id,
            "flight": watch.flight_label,
            "watch_status": watch.status,
            "total_fetches": sum(1 for e in timeline if e["type"] == "fetch"),
            "webhook_events_received": sum(1 for e in timeline if e["type"] == "webhook_event"),
            "latest_fetch_at": next(
                (e["at"] for e in reversed(timeline) if e["type"] == "fetch"), None
            ),
            "latest_source": next(
                (e.get("source") for e in reversed(timeline) if e["type"] == "fetch"), None
            ),
            "timeline": timeline,
        }
        return json.dumps(summary, default=str)

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
            return str(result.get("error", "Could not cancel watch."))
        return str(result.get("message", "Watch cancelled."))
