from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timezone
from typing import Any

import httpx
from pydantic_ai import Agent, RunContext

from app.agent.agent import AgentDeps

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# ICS fetch + parse helpers
# ---------------------------------------------------------------------------


async def _fetch_ics(url: str) -> str:
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(url)
        resp.raise_for_status()
    return resp.text


def _parse_events(ics_text: str, start: date, end: date) -> list[dict[str, Any]]:
    """Return a sorted list of event dicts for the given date range."""
    import icalendar
    import recurring_ical_events

    cal = icalendar.Calendar.from_ical(ics_text)
    raw = recurring_ical_events.of(cal).between(start, end)

    events: list[dict[str, Any]] = []
    for e in raw:
        dt_prop = e.get("DTSTART")
        if dt_prop is None:
            continue
        dt = dt_prop.dt
        # Normalize to datetime; all-day events stay as date
        if isinstance(dt, datetime):
            dt = dt.astimezone(timezone.utc)
        events.append(
            {
                "dt": dt,
                "summary": str(e.get("SUMMARY", "")).strip(),
                "location": str(e.get("LOCATION", "")).strip() or None,
            }
        )

    return sorted(  # type: ignore[arg-type]
        events,
        key=lambda x: x["dt"]
        if isinstance(x["dt"], datetime)
        else datetime.combine(x["dt"], datetime.min.time(), tzinfo=timezone.utc),
    )


def _format_event(ev: dict[str, Any], member_label: str | None) -> str:
    dt = ev["dt"]
    if isinstance(dt, datetime):
        day_str = dt.strftime("%A %-d %b")
        time_str = dt.strftime("%H:%M")
    else:
        day_str = dt.strftime("%A %-d %b")  # type: ignore[union-attr]
        time_str = "all day"

    summary = ev["summary"] or "(no title)"
    loc = f" ({ev['location']})" if ev["location"] else ""
    prefix = f"{member_label}: " if member_label else ""
    return f"{day_str} {time_str} — {prefix}{summary}{loc}"


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------


def register_calendar_tools(agent: Agent[AgentDeps, str]) -> None:
    """Attach calendar tools to the conversation agent."""

    @agent.tool
    async def add_calendar(
        ctx: RunContext[AgentDeps],
        name: str,
        url: str,
        member_name: str | None = None,
        category: str = "general",
    ) -> str:
        """Add a new ICS calendar for the household.

        Use this when the user wants to register a calendar URL so the agent can
        query it for upcoming events (matches, training, school events, etc.).

        Args:
            name: A descriptive name, e.g. "Sondre Football".
            url: The ICS/iCal URL to fetch events from.
            member_name: Optional household member this calendar belongs to,
                         e.g. "Sondre". Used to filter events by person.
            category: Optional grouping, e.g. "sports", "school", "general".
        """
        from sqlmodel import select

        from app.db import users_session
        from app.models.calendars import Calendar

        # Validate the URL is reachable
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(url)
                resp.raise_for_status()
        except Exception:
            return "Could not reach the calendar URL — check it is correct and accessible."

        with users_session() as session:
            # Prevent duplicate URLs within the same household
            existing = session.exec(
                select(Calendar).where(
                    Calendar.household_id == ctx.deps.household_id,
                    Calendar.url == url,
                )
            ).first()
            if existing:
                return (
                    f"A calendar with this URL already exists: "
                    f"'{existing.name}' (ID: {existing.id})"
                )

            cal = Calendar(
                household_id=ctx.deps.household_id,
                name=name,
                url=url,
                member_name=member_name,
                category=category,
            )
            session.add(cal)
            session.commit()
            session.refresh(cal)
            cal_id = cal.id

        member_info = f", member: {member_name}" if member_name else ""
        return f"Calendar '{name}' added (ID: {cal_id}{member_info})."

    @agent.tool
    async def list_calendars(ctx: RunContext[AgentDeps]) -> str:
        """List all calendars registered for this household."""
        from sqlmodel import select

        from app.db import users_session
        from app.models.calendars import Calendar

        with users_session() as session:
            cals = session.exec(
                select(Calendar).where(Calendar.household_id == ctx.deps.household_id)
            ).all()

        if not cals:
            return "No calendars registered. Use add_calendar to add one."

        lines = []
        for c in cals:
            member = f", member: {c.member_name}" if c.member_name else ""
            lines.append(f"• {c.name} [{c.category}{member}] — ID: {c.id}")
        return "Calendars:\n" + "\n".join(lines)

    @agent.tool
    async def remove_calendar(ctx: RunContext[AgentDeps], calendar_id: str) -> str:
        """Remove a calendar by its ID.

        Args:
            calendar_id: The ID returned by list_calendars or add_calendar.
        """
        from app.db import users_session
        from app.models.calendars import Calendar

        with users_session() as session:
            cal = session.get(Calendar, calendar_id)
            if cal is None or cal.household_id != ctx.deps.household_id:
                return f"Calendar '{calendar_id}' not found."
            name = cal.name
            session.delete(cal)
            session.commit()

        return f"Calendar '{name}' removed."

    @agent.tool
    async def get_calendar_events(
        ctx: RunContext[AgentDeps],
        start_iso: str,
        end_iso: str,
        member_name: str | None = None,
    ) -> str:
        """Fetch and return events from household calendars for a date range.

        Use this whenever the user asks about upcoming events, matches, training
        sessions, or schedules — for any household member or all members.

        Args:
            start_iso: Start date as ISO-8601, e.g. "2026-03-10".
            end_iso: End date (inclusive), e.g. "2026-03-16".
            member_name: Optional — only return events from calendars belonging
                         to this household member (case-insensitive match).
                         Omit to include all members.
        """
        from sqlmodel import select

        from app.db import users_session
        from app.models.calendars import Calendar

        try:
            start = date.fromisoformat(start_iso)
            end = date.fromisoformat(end_iso)
        except ValueError:
            return "Invalid date format. Use ISO-8601, e.g. '2026-03-10'."

        if end < start:
            return "end_iso must be on or after start_iso."

        with users_session() as session:
            query = select(Calendar).where(Calendar.household_id == ctx.deps.household_id)
            cals = session.exec(query).all()

        if not cals:
            return "No calendars registered. Use add_calendar to add one."

        # Filter by member name if requested
        if member_name:
            mn_lower = member_name.lower()
            cals = [c for c in cals if c.member_name and c.member_name.lower() == mn_lower]
            if not cals:
                return f"No calendars found for member '{member_name}'."

        # Fetch all calendars concurrently
        async def _fetch_one(cal: Calendar) -> tuple[Calendar, list[dict[str, Any]]]:
            try:
                ics_text = await _fetch_ics(cal.url)
                events = _parse_events(ics_text, start, end)
                return cal, events
            except Exception as exc:
                logger.warning("Failed to fetch calendar '%s': %s", cal.name, exc)
                return cal, []

        results = await asyncio.gather(*[_fetch_one(c) for c in cals])

        # Merge and sort all events across calendars, tagging with member name
        all_events: list[tuple[Any, str | None, str]] = []
        for cal, events in results:
            for ev in events:
                all_events.append((ev["dt"], cal.member_name, _format_event(ev, cal.member_name)))

        all_events.sort(  # type: ignore[arg-type]
            key=lambda x: x[0]
            if isinstance(x[0], datetime)
            else datetime.combine(x[0], datetime.min.time(), tzinfo=timezone.utc),
        )

        if not all_events:
            member_clause = f" for {member_name}" if member_name else ""
            return f"No events found{member_clause} between {start_iso} and {end_iso}."

        lines = [line for _, _, line in all_events]
        return "\n".join(lines)
