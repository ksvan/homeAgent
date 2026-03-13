from __future__ import annotations

import logging
from datetime import datetime, timezone

from pydantic_ai import Agent, RunContext

from app.agent.agent import AgentDeps

logger = logging.getLogger(__name__)


def register_action_tools(agent: Agent[AgentDeps, str]) -> None:
    """Attach scheduled-action tools to the conversation agent."""

    @agent.tool
    async def schedule_homey_action(
        ctx: RunContext[AgentDeps],
        description: str,
        tool_name: str,
        tool_args: dict[str, object],
        run_at_iso: str,
    ) -> str:
        """Schedule a Homey device action to execute automatically at a specific time.

        Use this when the user asks to control a device at a future time,
        e.g. "turn on the bedroom light at 07:30 tomorrow" or
        "switch off the garden lights at 23:00".

        Do NOT use set_reminder for this — set_reminder only sends a text message.
        This tool actually executes the device action at the scheduled time.

        Workflow — follow these steps in order:
        1. Call homey_search_tools to find the right tool for the device and action.
        2. Note the exact tool name returned by the search (e.g. "set_devices_capabilities_values").
        3. Call this tool with:
           - tool_name: always "homey_use_tool"
           - tool_args: the EXACT same {"name": ..., "arguments": ...} you would pass
             to homey_use_tool for an immediate action. Use the tool name from step 2.

        Example structure (do NOT copy these placeholder values — use real values from search results):
          schedule_homey_action(
              description="Turn off garden lights",
              tool_name="homey_use_tool",
              tool_args={"name": "<TOOL_NAME_FROM_SEARCH>", "arguments": {"<ARG>": "<VALUE>"}},
              run_at_iso="2026-03-04T23:00:00+01:00",
          )

        Args:
            description: Human-readable summary, e.g. "Turn on bedroom light".
            tool_name: Always "homey_use_tool".
            tool_args: Exactly what you would pass to homey_use_tool right now:
                       {"name": "<tool_name_from_search>", "arguments": {<real_device_args>}}.
                       Never guess the inner tool name — always get it from homey_search_tools.
            run_at_iso: When to execute, as an ISO-8601 datetime string with timezone,
                        e.g. "2026-03-03T07:30:00+01:00". Always include a UTC offset.
        """
        from app.policy.gate import evaluate_policy
        from app.scheduler.actions import schedule_action

        # Check policy at schedule time — high-impact tools cannot run unattended.
        inner_name = str(tool_args.get("name", "")).removeprefix("homey_")
        if inner_name:
            inner_args = dict(tool_args.get("arguments", {}))  # type: ignore[arg-type]
            decision = evaluate_policy(inner_name, inner_args)
            if decision.requires_confirm:
                return (
                    f"Cannot schedule '{description}': this action requires real-time "
                    "confirmation and cannot run unattended. Run it directly via chat instead."
                )

        try:
            run_at = datetime.fromisoformat(run_at_iso.replace("Z", "+00:00"))
        except ValueError:
            return (
                f"Invalid datetime format: {run_at_iso!r}. "
                "Use ISO-8601 with timezone, e.g. '2026-03-03T07:30:00+01:00'."
            )

        if run_at.tzinfo is None:
            run_at = run_at.replace(tzinfo=timezone.utc)

        if run_at <= datetime.now(timezone.utc):
            return "The requested time is in the past — please provide a future datetime."

        try:
            task_id = await schedule_action(
                user_id=ctx.deps.user_id,
                household_id=ctx.deps.household_id,
                channel_user_id=ctx.deps.channel_user_id,
                description=description,
                tool_name=tool_name,
                tool_args=tool_args,
                run_at=run_at,
            )
        except RuntimeError as exc:
            return f"Failed to schedule action: {exc}"
        friendly = run_at.strftime("%A, %d %B %Y at %H:%M %Z")
        return f"Scheduled: '{description}' for {friendly}. (ID: {task_id})"

    @agent.tool
    async def list_scheduled_actions(ctx: RunContext[AgentDeps]) -> str:
        """List all active scheduled Homey device actions for the current user."""
        import json

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

        actions = []
        for task in tasks:
            ctx_data = json.loads(task.context)
            if "action_tool" not in ctx_data:
                continue
            scheduled_at = ctx_data.get("scheduled_at", "unknown time")
            desc = ctx_data.get("action_description", task.title)
            actions.append(f"• {desc} — at {scheduled_at} (ID: {task.id})")

        if not actions:
            return "No scheduled device actions."
        return "Scheduled actions:\n" + "\n".join(actions)

    @agent.tool
    async def cancel_scheduled_action(
        ctx: RunContext[AgentDeps],
        task_id: str,
    ) -> str:
        """Cancel a scheduled Homey device action by its ID.

        Args:
            task_id: The action ID returned by schedule_homey_action or list_scheduled_actions.
        """
        from app.db import users_session
        from app.models.tasks import Task
        from app.scheduler.engine import get_scheduler

        with users_session() as session:
            task = session.get(Task, task_id)
            if task is None or task.user_id != ctx.deps.user_id:
                return f"Scheduled action {task_id!r} not found."
            if task.status != "ACTIVE":
                return f"Action {task_id!r} is already {task.status.lower()}."
            task.status = "CANCELLED"
            from datetime import datetime, timezone

            task.completed_at = datetime.now(timezone.utc)
            session.add(task)
            session.commit()

        scheduler = get_scheduler()
        if scheduler is not None:
            import asyncio

            async def _remove() -> None:
                try:
                    await scheduler.remove_schedule(task_id)
                except Exception:
                    pass

            asyncio.ensure_future(_remove())

        return "Scheduled action cancelled."
