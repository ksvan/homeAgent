from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.flights.models import FlightStatusChange, FlightStatusSnapshot, FlightWatch


def compute_changes(
    watch: "FlightWatch",
    previous: "FlightStatusSnapshot | None",
    current: "FlightStatusSnapshot",
    notify_policy: dict,  # type: ignore[type-arg]
) -> list["FlightStatusChange"]:
    """Return a list of meaningful FlightStatusChange objects between two snapshots.

    Returns an empty list when there is nothing worth notifying about.
    """
    from app.flights.models import FlightStatusChange

    if previous is None:
        # First snapshot — no diff yet; do not notify unless already in bad state
        if current.cancelled:
            return [FlightStatusChange(
                watch_id=watch.id,
                flight_label=watch.flight_label,
                change_type="cancelled",
                severity="critical",
                summary=f"{watch.flight_label} was cancelled before monitoring started.",
                old_values={},
                new_values={"cancelled": True},
            )]
        return []

    changes: list[FlightStatusChange] = []
    delay_threshold = int(notify_policy.get("delay_threshold_minutes", 15))

    # Cancellation
    if notify_policy.get("notify_cancellations", True):
        if not previous.cancelled and current.cancelled:
            changes.append(FlightStatusChange(
                watch_id=watch.id,
                flight_label=watch.flight_label,
                change_type="cancelled",
                severity="critical",
                summary=f"{watch.flight_label} has been cancelled.",
                old_values={"cancelled": False},
                new_values={"cancelled": True},
            ))

    # Diversion
    if notify_policy.get("notify_diversions", True):
        if not previous.diverted and current.diverted:
            changes.append(FlightStatusChange(
                watch_id=watch.id,
                flight_label=watch.flight_label,
                change_type="diverted",
                severity="critical",
                summary=(
                    f"{watch.flight_label} has been diverted"
                    + (f" to {current.diversion_airport}" if current.diversion_airport else "")
                    + "."
                ),
                old_values={"diverted": False},
                new_values={"diverted": True, "diversion_airport": current.diversion_airport},
            ))

    # Delay change
    prev_delay = previous.delay_minutes or 0
    curr_delay = current.delay_minutes or 0
    if curr_delay >= delay_threshold and abs(curr_delay - prev_delay) >= delay_threshold:
        changes.append(FlightStatusChange(
            watch_id=watch.id,
            flight_label=watch.flight_label,
            change_type="delay_changed",
            severity="warning" if curr_delay < 60 else "critical",
            summary=f"{watch.flight_label} is delayed {curr_delay} minutes.",
            old_values={"delay_minutes": prev_delay},
            new_values={"delay_minutes": curr_delay},
        ))
    elif prev_delay >= delay_threshold and curr_delay < delay_threshold:
        # Delay resolved
        changes.append(FlightStatusChange(
            watch_id=watch.id,
            flight_label=watch.flight_label,
            change_type="delay_resolved",
            severity="info",
            summary=f"{watch.flight_label} delay has been reduced to {curr_delay} minutes.",
            old_values={"delay_minutes": prev_delay},
            new_values={"delay_minutes": curr_delay},
        ))

    # Gate change
    if notify_policy.get("notify_gate_changes", True):
        if (
            previous.departure_gate
            and current.departure_gate
            and previous.departure_gate != current.departure_gate
        ):
            changes.append(FlightStatusChange(
                watch_id=watch.id,
                flight_label=watch.flight_label,
                change_type="gate_changed",
                severity="warning",
                summary=(
                    f"{watch.flight_label} gate changed from "
                    f"{previous.departure_gate} to {current.departure_gate}."
                ),
                old_values={"departure_gate": previous.departure_gate},
                new_values={"departure_gate": current.departure_gate},
            ))
        elif not previous.departure_gate and current.departure_gate:
            changes.append(FlightStatusChange(
                watch_id=watch.id,
                flight_label=watch.flight_label,
                change_type="gate_assigned",
                severity="warning",
                summary=f"{watch.flight_label} departure gate assigned: {current.departure_gate}.",
                old_values={"departure_gate": None},
                new_values={"departure_gate": current.departure_gate},
            ))

    # Terminal change
    if notify_policy.get("notify_terminal_changes", True):
        if (
            previous.departure_terminal
            and current.departure_terminal
            and previous.departure_terminal != current.departure_terminal
        ):
            changes.append(FlightStatusChange(
                watch_id=watch.id,
                flight_label=watch.flight_label,
                change_type="terminal_changed",
                severity="warning",
                summary=(
                    f"{watch.flight_label} terminal changed from "
                    f"{previous.departure_terminal} to {current.departure_terminal}."
                ),
                old_values={"departure_terminal": previous.departure_terminal},
                new_values={"departure_terminal": current.departure_terminal},
            ))

    # Boarding status
    if notify_policy.get("notify_boarding", True):
        if previous.state != "BOARDING" and current.state == "BOARDING":
            changes.append(FlightStatusChange(
                watch_id=watch.id,
                flight_label=watch.flight_label,
                change_type="boarding_started",
                severity="warning",
                summary=f"{watch.flight_label} is now boarding.",
                old_values={"state": previous.state},
                new_values={"state": "BOARDING"},
            ))

    # Baggage carousel
    if not previous.baggage_claim and current.baggage_claim:
        changes.append(FlightStatusChange(
            watch_id=watch.id,
            flight_label=watch.flight_label,
            change_type="baggage_assigned",
            severity="info",
            summary=f"{watch.flight_label} baggage claim: {current.baggage_claim}.",
            old_values={"baggage_claim": None},
            new_values={"baggage_claim": current.baggage_claim},
        ))

    return changes


def should_notify(change: "FlightStatusChange", notify_policy: dict) -> bool:  # type: ignore[type-arg]
    """Return True if this change should produce a user notification."""
    quiet_hours_mode = notify_policy.get("quiet_hours_mode", "urgent_only")

    if quiet_hours_mode == "urgent_only":
        return change.severity in ("critical", "warning")
    return True
