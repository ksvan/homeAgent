from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from pydantic_ai import Agent, RunContext

from app.agent.agent import AgentDeps

logger = logging.getLogger(__name__)


def register_reminder_tools(agent: Agent[AgentDeps, str]) -> None:
    """Attach reminder tools to the conversation agent."""

    @agent.tool
    async def set_reminder(
        ctx: RunContext[AgentDeps],
        text: str,
        run_at_iso: str,
    ) -> str:
        """Schedule a reminder to be sent to the user at a specific time.

        Args:
            text: The reminder message text to deliver to the user.
            run_at_iso: When to fire the reminder, as an ISO-8601 datetime
                        string including timezone offset or 'Z' for UTC.
                        Example: "2026-03-01T15:00:00Z"
        """
        from app.scheduler.reminders import schedule_reminder

        try:
            run_at = datetime.fromisoformat(run_at_iso.replace("Z", "+00:00"))
        except ValueError:
            return (
                f"Invalid datetime format: {run_at_iso!r}. "
                "Use ISO-8601, e.g. '2026-03-01T15:00:00Z'."
            )

        if run_at.tzinfo is None:
            run_at = run_at.replace(tzinfo=timezone.utc)

        now = datetime.now(timezone.utc)
        if run_at <= now:
            return "The requested time is in the past — please provide a future datetime."

        try:
            task_id = await schedule_reminder(
                user_id=ctx.deps.user_id,
                household_id=ctx.deps.household_id,
                channel_user_id=ctx.deps.channel_user_id,
                text=text,
                run_at=run_at,
            )
        except RuntimeError as exc:
            return f"Failed to set reminder: {exc}"
        friendly = run_at.strftime("%A, %d %B %Y at %H:%M UTC")
        return f"Reminder set for {friendly}. (ID: {task_id})"

    @agent.tool
    async def list_reminders(ctx: RunContext[AgentDeps]) -> str:
        """List all active (pending) reminders for the current user."""
        from sqlmodel import select

        from app.db import users_session
        from app.models.tasks import Task

        with users_session() as session:
            tasks = session.exec(
                select(Task).where(
                    Task.user_id == ctx.deps.user_id,
                    Task.status == "ACTIVE",
                )
            ).all()

        reminders = []
        for task in tasks:
            ctx_data = json.loads(task.context)
            if "reminder_text" not in ctx_data:
                continue  # not a reminder task
            scheduled_at = ctx_data.get("scheduled_at", "unknown time")
            reminders.append(f"• {task.title} — due {scheduled_at} (ID: {task.id})")

        if not reminders:
            return "You have no active reminders."
        return "Active reminders:\n" + "\n".join(reminders)

    @agent.tool
    async def cancel_reminder(
        ctx: RunContext[AgentDeps],
        task_id: str,
    ) -> str:
        """Cancel an active reminder by its ID.

        Args:
            task_id: The reminder task ID returned by set_reminder or list_reminders.
        """
        # Verify the reminder belongs to this user
        from app.db import users_session
        from app.models.tasks import Task
        from app.scheduler.reminders import cancel_reminder as _cancel

        with users_session() as session:
            task = session.get(Task, task_id)
            if task is None or task.user_id != ctx.deps.user_id:
                return f"Reminder {task_id!r} not found."
            if task.status != "ACTIVE":
                return f"Reminder {task_id!r} is already {task.status.lower()}."

        cancelled = _cancel(task_id)
        if cancelled:
            return "Reminder cancelled."
        return f"Could not cancel reminder {task_id!r} — it may have already fired."
