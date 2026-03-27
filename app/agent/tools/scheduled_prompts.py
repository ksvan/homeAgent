from __future__ import annotations

import logging

from pydantic_ai import Agent, RunContext

from app.agent.agent import AgentDeps

logger = logging.getLogger(__name__)

def register_scheduled_prompt_tools(agent: Agent[AgentDeps, str]) -> None:
    """Attach scheduled-prompt tools to the conversation agent."""

    @agent.tool
    async def schedule_prompt(
        ctx: RunContext[AgentDeps],
        name: str,
        prompt: str,
        recurrence: str,
        time_of_day: str,
        run_at: str | None = None,
    ) -> str:
        """Schedule a prompt that runs the agent automatically and sends the response.

        Supports both recurring and one-time (run-once) schedules.

        Use this when the user asks to set up a repeating or one-off task, e.g.:
          - "Remind me every Sunday at 20:00 what matches Sondre has next week"
          - "Give me a daily briefing every morning at 07:30"
          - "Send me an energy report tomorrow at 09:00"

        Args:
            name: Short descriptive label, e.g. "Weekly Sondre football summary".
            prompt: The exact prompt text to run, e.g.
                    "What football matches does Sondre have next week? Summarize briefly."
            recurrence: One of:
                - "daily"            — every day
                - "weekly:<day>"     — e.g. "weekly:sun", "weekly:mon", "weekly:wed"
                - "monthly:<day>"    — e.g. "monthly:1", "monthly:15"
                - "once"             — fire once at run_at, then auto-delete
                Day abbreviations: mon tue wed thu fri sat sun
            time_of_day: Time in 24h HH:MM format, e.g. "20:00" or "07:30".
                         Ignored when recurrence="once" (use run_at instead).
            run_at: Required when recurrence="once". ISO 8601 datetime with timezone
                    offset, e.g. "2026-03-28T09:00:00+01:00". Ignored for recurring.
        """
        from datetime import datetime

        from app.scheduler.scheduled_prompts import create_scheduled_prompt, recurrence_label

        parsed_run_at = None
        if recurrence == "once":
            if not run_at:
                return "Could not schedule prompt: run_at is required when recurrence='once'"
            try:
                parsed_run_at = datetime.fromisoformat(run_at)
            except ValueError as exc:
                return f"Could not schedule prompt: invalid run_at — {exc}"

        try:
            prompt_id = await create_scheduled_prompt(
                user_id=ctx.deps.user_id,
                household_id=ctx.deps.household_id,
                channel_user_id=ctx.deps.channel_user_id,
                name=name,
                prompt=prompt,
                recurrence=recurrence,
                time_of_day=time_of_day,
                run_at=parsed_run_at,
            )
        except (ValueError, RuntimeError) as exc:
            return f"Could not schedule prompt: {exc}"

        label = recurrence_label(recurrence, time_of_day, parsed_run_at)
        return f"Scheduled '{name}' — {label}. (ID: {prompt_id})"

    @agent.tool
    async def list_scheduled_prompts(ctx: RunContext[AgentDeps]) -> str:
        """List all active scheduled prompts for this household."""
        from sqlmodel import select

        from app.db import users_session
        from app.models.scheduled_prompts import ScheduledPrompt
        from app.scheduler.scheduled_prompts import recurrence_label

        with users_session() as session:
            prompts = session.exec(
                select(ScheduledPrompt).where(
                    ScheduledPrompt.household_id == ctx.deps.household_id,
                    ScheduledPrompt.enabled == True,  # noqa: E712
                )
            ).all()

        if not prompts:
            return "No scheduled prompts. Use schedule_prompt to create one."

        lines = []
        for sp in prompts:
            label = recurrence_label(sp.recurrence, sp.time_of_day, sp.run_at)
            lines.append(f"• {sp.name} — {label} | ID: {sp.id}")
        return "Scheduled prompts:\n" + "\n".join(lines)

    @agent.tool
    async def cancel_scheduled_prompt(
        ctx: RunContext[AgentDeps],
        prompt_id: str,
    ) -> str:
        """Cancel (disable) a recurring scheduled prompt by its ID.

        Args:
            prompt_id: The ID returned by schedule_prompt or list_scheduled_prompts.
        """
        from app.db import users_session
        from app.models.scheduled_prompts import ScheduledPrompt
        from app.scheduler.scheduled_prompts import remove_scheduled_prompt

        with users_session() as session:
            sp = session.get(ScheduledPrompt, prompt_id)
            if sp is None or sp.household_id != ctx.deps.household_id:
                return f"Scheduled prompt '{prompt_id}' not found."
            if not sp.enabled:
                return f"Scheduled prompt '{sp.name}' is already cancelled."
            name = sp.name

        remove_scheduled_prompt(prompt_id)
        return f"Cancelled scheduled prompt '{name}'."
