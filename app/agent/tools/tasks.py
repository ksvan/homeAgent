"""Agent tools for multi-step task management."""

from __future__ import annotations

import json
import logging

from pydantic_ai import Agent, RunContext

from app.agent.agent import AgentDeps

logger = logging.getLogger(__name__)


def register_task_tools(agent: Agent[AgentDeps, str]) -> None:
    """Attach multi-step task tools to the conversation agent."""

    from app.config import get_settings

    if not get_settings().features.multi_step_tasks:
        return

    @agent.tool
    async def create_task(
        ctx: RunContext[AgentDeps],
        title: str,
        task_kind: str,
        summary: str,
        steps: str,
    ) -> str:
        """Create a multi-step task to track work that spans multiple turns.

        Only create a task when the user's goal:
          - Spans multiple conversation turns
          - Requires gathering info, then waiting for a decision
          - Involves a future follow-up or time-based wait
          - Needs partial progress to be explicitly remembered

        Do NOT create a task for:
          - One-shot factual answers
          - Single immediate tool actions (use the tool directly)
          - Simple reminders (use set_reminder instead)

        Args:
            title: Short descriptive title for the task.
            task_kind: One of "plan" (compare/recommend/choose), "track" (monitor
                       over time), "prepare" (gather info for future need),
                       "handoff" (queue a future action as part of larger work).
            summary: One-sentence description of current progress.
            steps: JSON array of step objects, each with "title" (str) and
                   "step_type" (one of "research", "decision", "tool", "wait",
                   "message"). Example:
                   [{"title": "Gather options", "step_type": "research"},
                    {"title": "User chooses", "step_type": "decision"}]
        """
        from app.control.events import emit
        from app.tasks.repository import TaskRepository

        try:
            step_list = json.loads(steps)
            if not isinstance(step_list, list):
                return "steps must be a JSON array of {title, step_type} objects."
        except json.JSONDecodeError:
            return "steps must be valid JSON."

        valid_kinds = {"plan", "track", "prepare", "handoff"}
        if task_kind not in valid_kinds:
            return f"task_kind must be one of: {', '.join(sorted(valid_kinds))}"

        repo = TaskRepository()
        task = repo.create_task(
            household_id=ctx.deps.household_id,
            user_id=ctx.deps.user_id,
            title=title,
            task_kind=task_kind,
            summary=summary,
            steps=step_list,
        )
        task = repo.update_task(task.id, last_agent_run_id=ctx.deps.run_id)

        emit(
            "task.create",
            {
                "task_id": task.id,
                "title": title,
                "task_kind": task_kind,
                "summary": summary,
                "step_count": len(step_list),
            },
            run_id=ctx.deps.run_id,
        )

        return f"Task created: {title} (ID: {task.id})"

    @agent.tool
    async def update_task_progress(
        ctx: RunContext[AgentDeps],
        task_id: str,
        summary: str,
        step_updates: str = "[]",
        context_patch: str = "{}",
    ) -> str:
        """Update progress on an existing multi-step task.

        Call this whenever you make meaningful progress on a task — completing
        a step, gathering new information, or narrowing down options.
        Always keep the summary up to date so it reflects current state.

        Args:
            task_id: The task ID returned by create_task.
            summary: Updated one-sentence progress summary.
            step_updates: JSON array of step updates, each with "step_index" (int)
                         and "status" ("active"|"done"|"failed"|"cancelled").
                         Optional "details" (str) for extra context.
                         Example: [{"step_index": 0, "status": "done"}]
            context_patch: JSON object to merge into the task's working context.
                          Use for structured intermediate results (options, IDs, etc.).
        """
        from app.control.events import emit
        from app.tasks.repository import TaskRepository

        repo = TaskRepository()
        task = repo.get_task(task_id)
        if task is None or task.user_id != ctx.deps.user_id:
            return f"Task {task_id} not found."

        # Update summary and run link
        updates: dict[str, object] = {
            "summary": summary,
            "last_agent_run_id": ctx.deps.run_id,
        }

        # Merge context patch
        try:
            patch = json.loads(context_patch)
            if patch:
                existing = json.loads(task.context or "{}")
                existing.update(patch)
                updates["context"] = json.dumps(existing)
        except json.JSONDecodeError:
            pass

        repo.update_task(task_id, **updates)

        # Apply step updates
        try:
            step_updates_list = json.loads(step_updates)
            if isinstance(step_updates_list, list):
                steps = repo.get_steps(task_id)
                step_by_index = {s.step_index: s for s in steps}
                for su in step_updates_list:
                    idx = su.get("step_index")
                    status = su.get("status")
                    if idx is not None and status and idx in step_by_index:
                        step = step_by_index[idx]
                        step_fields: dict[str, object] = {"status": status}
                        if su.get("details"):
                            step_fields["details_json"] = json.dumps({"note": su["details"]})
                        if status == "done":
                            from datetime import datetime, timezone
                            step_fields["completed_at"] = datetime.now(timezone.utc)
                        elif status == "active":
                            from datetime import datetime, timezone
                            step_fields["started_at"] = datetime.now(timezone.utc)
                        repo.update_step(step.id, **step_fields)
        except json.JSONDecodeError:
            pass

        emit(
            "task.update",
            {"task_id": task_id, "summary": summary},
            run_id=ctx.deps.run_id,
        )

        return f"Task updated: {summary}"

    @agent.tool
    async def await_task_input(
        ctx: RunContext[AgentDeps],
        task_id: str,
        prompt_hint: str,
    ) -> str:
        """Mark a task as blocked waiting for user input.

        Call this after presenting options or asking a question where you need
        the user to make a choice before you can continue.

        Args:
            task_id: The task ID.
            prompt_hint: Short description of what you're waiting for, e.g.
                        "Choose one of the 3 dinner options" or
                        "Confirm the Thursday pickup plan".
        """
        from app.control.events import emit
        from app.tasks.repository import TaskRepository

        repo = TaskRepository()
        task = repo.get_task(task_id)
        if task is None or task.user_id != ctx.deps.user_id:
            return f"Task {task_id} not found."

        try:
            repo.transition_status(task_id, "AWAITING_INPUT")
        except ValueError as exc:
            return str(exc)

        repo.update_task(
            task_id,
            awaiting_input_hint=prompt_hint,
            last_agent_run_id=ctx.deps.run_id,
        )

        emit(
            "task.await_input",
            {"task_id": task_id, "prompt_hint": prompt_hint},
            run_id=ctx.deps.run_id,
        )

        return f"Task paused — waiting for: {prompt_hint}"

    @agent.tool
    async def complete_task(
        ctx: RunContext[AgentDeps],
        task_id: str,
        summary: str = "",
    ) -> str:
        """Mark a multi-step task as completed.

        Call when the user's goal has been achieved.

        Args:
            task_id: The task ID.
            summary: Final outcome summary (optional, updates the task summary).
        """
        from app.control.events import emit
        from app.tasks.repository import TaskRepository

        repo = TaskRepository()
        task = repo.get_task(task_id)
        if task is None or task.user_id != ctx.deps.user_id:
            return f"Task {task_id} not found."

        try:
            repo.transition_status(task_id, "COMPLETED")
        except ValueError as exc:
            return str(exc)

        if summary:
            repo.update_task(task_id, summary=summary)

        emit(
            "task.complete",
            {"task_id": task_id, "summary": summary or task.summary or ""},
            run_id=ctx.deps.run_id,
        )

        return "Task completed."

    @agent.tool
    async def cancel_task(
        ctx: RunContext[AgentDeps],
        task_id: str,
        reason: str = "",
    ) -> str:
        """Cancel a multi-step task.

        Call when the user explicitly asks to stop or abandon a task.

        Args:
            task_id: The task ID.
            reason: Why the task was cancelled (optional).
        """
        from app.control.events import emit
        from app.tasks.repository import TaskRepository

        repo = TaskRepository()
        task = repo.get_task(task_id)
        if task is None or task.user_id != ctx.deps.user_id:
            return f"Task {task_id} not found."

        try:
            repo.transition_status(task_id, "CANCELLED")
        except ValueError as exc:
            return str(exc)

        if reason:
            repo.update_task(task_id, summary=f"Cancelled: {reason}")

        emit(
            "task.cancel",
            {"task_id": task_id, "reason": reason},
            run_id=ctx.deps.run_id,
        )

        return "Task cancelled."

    @agent.tool
    async def list_tasks(ctx: RunContext[AgentDeps]) -> str:
        """List all active (non-completed) tasks for the current user.

        Use this to check what tasks are in progress before creating new ones
        or when the user asks about ongoing work.
        """
        from app.tasks.repository import TaskRepository

        repo = TaskRepository()
        tasks = repo.get_active_tasks(ctx.deps.user_id)

        if not tasks:
            return "No active tasks."

        lines = []
        for t in tasks:
            kind = t.task_kind or "legacy"
            hint = (
                f" — waiting for: {t.awaiting_input_hint}"
                if t.awaiting_input_hint and t.status == "AWAITING_INPUT"
                else ""
            )
            lines.append(f"• [{kind}] {t.title} — {t.status}{hint} (ID: {t.id})")
            if t.summary:
                lines.append(f"  Summary: {t.summary}")

        return "Active tasks:\n" + "\n".join(lines)

    @agent.tool
    async def schedule_task_resume(
        ctx: RunContext[AgentDeps],
        task_id: str,
        resume_at_iso: str,
        reason: str,
    ) -> str:
        """Schedule a future follow-up for a task.

        Use this when a task needs to check back at a specific time, for example:
        - "Remind me if I haven't done X by Sunday"
        - "Check back on this tomorrow morning"
        - "Follow up on this in 2 hours"

        The agent will be re-invoked at the scheduled time with the task context.

        Args:
            task_id: The task ID.
            resume_at_iso: When to resume, as ISO-8601 datetime with timezone.
                          Example: "2026-03-30T09:00:00+01:00"
            reason: Why the follow-up is scheduled (shown to user and in task context).
        """
        from datetime import datetime, timezone

        from app.control.events import emit
        from app.tasks.repository import TaskRepository

        repo = TaskRepository()
        task = repo.get_task(task_id)
        if task is None or task.user_id != ctx.deps.user_id:
            return f"Task {task_id} not found."

        try:
            resume_at = datetime.fromisoformat(resume_at_iso.replace("Z", "+00:00"))
        except ValueError:
            return f"Invalid datetime: {resume_at_iso!r}. Use ISO-8601."

        if resume_at.tzinfo is None:
            resume_at = resume_at.replace(tzinfo=timezone.utc)

        now = datetime.now(timezone.utc)
        if resume_at <= now:
            return "The requested time is in the past."

        # Transition to AWAITING_INPUT and store resume info
        try:
            repo.transition_status(task_id, "AWAITING_INPUT")
        except ValueError as exc:
            return str(exc)

        repo.update_task(
            task_id,
            awaiting_input_hint=reason,
            resume_after=resume_at,
            last_agent_run_id=ctx.deps.run_id,
        )

        from app.tasks.service import schedule_task_resume as _schedule

        await _schedule(
            task_id=task_id,
            resume_at=resume_at,
            user_id=ctx.deps.user_id,
            household_id=ctx.deps.household_id,
            channel_user_id=ctx.deps.channel_user_id,
        )

        emit(
            "task.schedule_resume",
            {"task_id": task_id, "resume_at": resume_at_iso, "reason": reason},
            run_id=ctx.deps.run_id,
        )

        friendly = resume_at.strftime("%A, %d %B %Y at %H:%M")
        return f"Task follow-up scheduled for {friendly}. Reason: {reason}"

    @agent.tool
    async def link_task_entity(
        ctx: RunContext[AgentDeps],
        task_id: str,
        entity_type: str,
        entity_name: str,
        role: str = "subject",
    ) -> str:
        """Link a world-model entity to a task for better context.

        Call when a task involves a specific household member, device, place,
        calendar, or routine. Linking helps the agent resume with full context.

        Args:
            task_id: The task ID.
            entity_type: One of "member", "place", "device", "calendar", "routine".
            entity_name: The name of the entity (e.g. "Sondre", "Office", "Hallway plug").
            role: Relationship role — "subject" (default), "source", or "target".
        """
        from app.control.events import emit
        from app.tasks.repository import TaskRepository
        from app.world.repository import WorldModelRepository

        repo = TaskRepository()
        task = repo.get_task(task_id)
        if task is None or task.user_id != ctx.deps.user_id:
            return f"Task {task_id} not found."

        valid_types = {"member", "place", "device", "calendar", "routine"}
        if entity_type not in valid_types:
            return f"entity_type must be one of: {', '.join(sorted(valid_types))}"

        # Resolve name → ID via world model
        wm = WorldModelRepository()
        finder = {
            "member": wm.find_member_by_name,
            "place": wm.find_place_by_name,
            "device": wm.find_device_by_name,
            "routine": wm.find_routine_by_name,
        }.get(entity_type)

        entity_id = None
        if finder:
            entity = finder(task.household_id, entity_name)
            if entity:
                entity_id = entity.id

        if not entity_id:
            # Calendar entities or unresolved — store name as ID for display
            entity_id = entity_name

        repo.add_link(task_id, entity_type, entity_id, role)

        emit(
            "task.link",
            {
                "task_id": task_id,
                "entity_type": entity_type,
                "entity_name": entity_name,
                "role": role,
            },
            run_id=ctx.deps.run_id,
        )

        return f"Linked {entity_type} '{entity_name}' to task."
