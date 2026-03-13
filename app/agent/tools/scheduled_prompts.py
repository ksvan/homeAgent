from __future__ import annotations

import logging

from pydantic_ai import Agent, RunContext

from app.agent.agent import AgentDeps

logger = logging.getLogger(__name__)

# Accepted recurrence values shown to the agent
_RECURRENCE_EXAMPLES = "'daily', 'weekly:mon', 'weekly:sun', 'monthly:15'"


def register_scheduled_prompt_tools(agent: Agent[AgentDeps, str]) -> None:
    """Attach scheduled-prompt tools to the conversation agent."""

    @agent.tool
    async def schedule_prompt(
        ctx: RunContext[AgentDeps],
        name: str,
        prompt: str,
        recurrence: str,
        time_of_day: str,
    ) -> str:
        """Schedule a recurring prompt that runs the agent automatically and sends the response.

        Use this when the user asks to set up a repeating task driven by the agent,
        e.g. "Remind me every Sunday at 20:00 what matches Sondre has next week" or
        "Give me a daily briefing every morning at 07:30".

        The agent will be invoked with the exact `prompt` text at the scheduled time
        and the result will be sent to the user's channel automatically.

        Args:
            name: Short descriptive label, e.g. "Weekly Sondre football summary".
            prompt: The exact prompt text to run, e.g.
                    "What football matches does Sondre have next week? Summarize briefly."
            recurrence: One of:
                - "daily"            — every day
                - "weekly:<day>"     — e.g. "weekly:sun", "weekly:mon", "weekly:wed"
                - "monthly:<day>"    — e.g. "monthly:1", "monthly:15"
                Day abbreviations: mon tue wed thu fri sat sun
            time_of_day: Time in 24h HH:MM format, e.g. "20:00" or "07:30".
        """
        from app.scheduler.scheduled_prompts import create_scheduled_prompt, recurrence_label

        try:
            prompt_id = create_scheduled_prompt(
                user_id=ctx.deps.user_id,
                household_id=ctx.deps.household_id,
                channel_user_id=ctx.deps.channel_user_id,
                name=name,
                prompt=prompt,
                recurrence=recurrence,
                time_of_day=time_of_day,
            )
        except ValueError as exc:
            return f"Could not schedule prompt: {exc}"

        label = recurrence_label(recurrence, time_of_day)
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
            label = recurrence_label(sp.recurrence, sp.time_of_day)
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
