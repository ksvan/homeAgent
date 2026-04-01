from __future__ import annotations

import json
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
        behavior_kind: str | None = None,
        goal: str | None = None,
        skip_if_empty: bool = False,
        skip_if_unchanged: bool = False,
        linked_entities: str = "[]",
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
            behavior_kind: Optional. One of: "generic_prompt" (default),
                    "morning_briefing", "calendar_digest", "energy_summary",
                    "watch_check", "task_followup". Affects default delivery policy.
            goal: Optional. Short description of the purpose, e.g. "weekly football
                  digest for Sondre". Shown in admin and included in run context.
            skip_if_empty: If true, suppress delivery when the result is empty or
                    trivial. Good for digests and summaries. Default false.
            skip_if_unchanged: If true, suppress delivery when the result is identical
                    to the previous run. Good for recurring checks. Default false.
            linked_entities: Optional JSON array of entities to link to this prompt.
                    Each object has "entity_type" ("member", "calendar", "device",
                    "place", "routine"), "entity_name" (resolved to ID), and optional
                    "role" ("subject", "source", "target", "focus").
                    Example: [{"entity_type": "member", "entity_name": "Sondre"}]
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

        # Build delivery policy from explicit flags
        delivery_policy: dict | None = None
        if skip_if_empty or skip_if_unchanged:
            delivery_policy = {}
            if skip_if_empty:
                delivery_policy["skip_if_empty"] = True
            if skip_if_unchanged:
                delivery_policy["skip_if_unchanged"] = True

        # Resolve linked entity names to IDs
        resolved_links: list[dict] | None = None
        try:
            raw_links = json.loads(linked_entities)
            if isinstance(raw_links, list) and raw_links:
                resolved_links = _resolve_links(ctx.deps.household_id, raw_links)
        except json.JSONDecodeError:
            pass

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
                behavior_kind=behavior_kind,
                goal=goal,
                delivery_policy_json=json.dumps(delivery_policy) if delivery_policy else None,
                links=resolved_links,
            )
        except (ValueError, RuntimeError) as exc:
            return f"Could not schedule prompt: {exc}"

        label = recurrence_label(recurrence, time_of_day, parsed_run_at)
        parts = [f"Scheduled '{name}' — {label}."]
        if behavior_kind and behavior_kind != "generic_prompt":
            parts.append(f"Kind: {behavior_kind}.")
        if goal:
            parts.append(f"Goal: {goal}.")
        parts.append(f"(ID: {prompt_id})")
        return " ".join(parts)

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
            kind = sp.behavior_kind or "generic_prompt"
            status = f" | last: {sp.last_status}" if sp.last_status else ""
            goal_part = f" | goal: {sp.goal}" if sp.goal else ""
            lines.append(f"• [{kind}] {sp.name} — {label}{goal_part}{status} | ID: {sp.id}")
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

    @agent.tool
    async def preview_scheduled_prompt(
        ctx: RunContext[AgentDeps],
        prompt_id: str,
    ) -> str:
        """Show the resolved prompt envelope and delivery policy for a scheduled prompt.

        Use this to inspect what will be sent when a prompt fires, including the
        envelope, linked entities, and delivery policy — without actually firing it.

        Args:
            prompt_id: The ID of the scheduled prompt to preview.
        """
        from sqlmodel import col, select

        from app.db import users_session
        from app.models.scheduled_prompts import ScheduledPrompt, ScheduledPromptLink
        from app.scheduler.delivery import parse_delivery_policy
        from app.scheduler.envelope import build_prompt_envelope

        with users_session() as session:
            sp = session.get(ScheduledPrompt, prompt_id)
            if sp is None or sp.household_id != ctx.deps.household_id:
                return f"Scheduled prompt '{prompt_id}' not found."
            links = list(session.exec(
                select(ScheduledPromptLink).where(
                    col(ScheduledPromptLink.prompt_id) == prompt_id
                )
            ).all())

        policy = parse_delivery_policy(sp)
        envelope = build_prompt_envelope(sp, links=links)

        sections = [
            f"## Preview: {sp.name}",
            f"Kind: {sp.behavior_kind or 'generic_prompt'}",
            f"Goal: {sp.goal or '(none)'}",
            f"Last status: {sp.last_status or 'never run'}",
            f"Delivery policy: {json.dumps(policy)}",
            "",
            "--- Envelope that would be sent ---",
            envelope,
        ]
        return "\n".join(sections)


def _resolve_links(household_id: str, raw_links: list[dict]) -> list[dict]:
    """Resolve entity names to IDs via WorldModelRepository."""
    from app.world.repository import WorldModelRepository

    wm = WorldModelRepository()
    finders = {
        "member": wm.find_member_by_name,
        "place": wm.find_place_by_name,
        "device": wm.find_device_by_name,
        "routine": wm.find_routine_by_name,
    }

    resolved = []
    for link in raw_links:
        entity_type = link.get("entity_type", "")
        entity_name = link.get("entity_name", "")
        role = link.get("role", "subject")

        entity_id = entity_name  # fallback: use name as ID
        finder = finders.get(entity_type)
        if finder:
            entity = finder(household_id, entity_name)
            if entity:
                entity_id = entity.id

        resolved.append({
            "entity_type": entity_type,
            "entity_id": entity_id,
            "role": role,
        })
    return resolved
