from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.flights.models import FlightStatusChange, FlightWatch

logger = logging.getLogger(__name__)


async def dispatch_flight_update(
    watch: "FlightWatch",
    changes: "list[FlightStatusChange]",
) -> None:
    """Dispatch a background agent run for significant flight changes.

    Must be launched with asyncio.create_task() by the caller — the webhook
    handler must return before this resolves.
    """
    if not changes:
        return

    text = _build_context_block(watch, changes)
    await _run_and_deliver(watch, text, trigger="flight_update")


async def notify_watch_failed(watch: "FlightWatch", last_known_state: str | None = None) -> None:
    """Notify the user that HomeAgent has lost contact with the provider."""
    state_note = f" Last known status: {last_known_state}." if last_known_state else ""
    text = (
        f"## Flight Monitor — Watch Failed\n"
        f"- flight: {watch.flight_label}\n\n"
        f"HomeAgent has lost contact with the flight data provider after repeated errors "
        f"and has stopped monitoring {watch.flight_label}.{state_note} "
        f"Tell the user briefly and suggest they check the airline app or another flight tracker."
    )
    await _run_and_deliver(watch, text, trigger="flight_watch_failed")


async def notify_watch_cancelled(watch: "FlightWatch", reason: str = "airline") -> None:
    """Notify the user that the airline cancelled the flight or user stopped watching."""
    if reason == "user":
        text = (
            f"## Flight Monitor — Watch Cancelled\n"
            f"- flight: {watch.flight_label}\n\n"
            f"The user cancelled monitoring for {watch.flight_label}. "
            f"Send a brief confirmation."
        )
    else:
        text = (
            f"## Flight Monitor — Flight Cancelled\n"
            f"- flight: {watch.flight_label}\n\n"
            f"The airline has cancelled {watch.flight_label}. HomeAgent has stopped monitoring it. "
            f"Tell the user and suggest they check the airline app for rebooking options."
        )
    await _run_and_deliver(watch, text, trigger="flight_watch_cancelled")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _build_context_block(
    watch: "FlightWatch",
    changes: "list[FlightStatusChange]",
) -> str:
    lines = [
        "## Flight Monitor Update",
        f"- flight: {watch.flight_label}",
        f"- carrier: {watch.carrier_code}{watch.flight_number}",
        f"- date: {watch.scheduled_departure_date}",
        "",
        "Changes detected:",
    ]
    for change in changes:
        lines.append(f"- [{change.severity.upper()}] {change.summary}")
    lines.append("")
    lines.append(
        "Produce a short, practical update for the user. "
        "For critical changes, include what they should do next. "
        "For info-level changes, be brief and clear."
    )
    return "\n".join(lines)


async def _run_and_deliver(
    watch: "FlightWatch",
    text: str,
    trigger: str,
) -> None:
    """Acquire the per-user lock, run the agent, and deliver the response via channel."""
    try:
        from app.agent.runner import agent_run, get_user_run_lock
        from app.channels.registry import get_channel

        async with get_user_run_lock(watch.user_id):
            outcome = await agent_run(
                text=text,
                user_id=watch.user_id,
                household_id=watch.household_id,
                channel_user_id=watch.channel_user_id,
                trigger=trigger,
                save_history=False,
            )

        if outcome.success and outcome.response:
            channel = get_channel()
            if channel is not None:
                await channel.send_message(watch.channel_user_id, outcome.response)
    except Exception:
        logger.exception(
            "Failed to dispatch flight notification for watch %s (trigger=%s)",
            watch.id,
            trigger,
        )
