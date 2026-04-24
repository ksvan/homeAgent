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

    @agent.tool
    async def record_task_attempt(
        ctx: RunContext[AgentDeps],
        task_id: str,
        approach: str,
        result: str,
        result_note: str,
        next_action: str,
        retryable: bool = True,
    ) -> str:
        """Record one autonomous attempt on a task and update pursuit state.

        Call this after each autonomous action so the task retains a durable
        record of what was tried, what happened, and what to try next. Always
        call this before scheduling a follow-up with schedule_task_followup.

        Args:
            task_id: The task ID.
            approach: Short description of what was attempted this run.
            result: Outcome — one of "success", "partial", "failed", "blocked".
            result_note: One-sentence human-readable result summary.
            next_action: What should happen next if the task is not done.
            retryable: Whether the task can still make progress autonomously.
        """
        from app.control.events import emit
        from app.tasks.repository import TaskRepository

        valid_results = {"success", "partial", "failed", "blocked"}
        if result not in valid_results:
            return f"result must be one of: {', '.join(sorted(valid_results))}"

        repo = TaskRepository()
        task = repo.get_task(task_id)
        if task is None or task.user_id != ctx.deps.user_id:
            return f"Task {task_id} not found."

        try:
            ctx_data: dict[str, object] = json.loads(task.context or "{}")
        except json.JSONDecodeError:
            ctx_data = {}

        pursuit = ctx_data.get("pursuit", {})
        if not isinstance(pursuit, dict):
            pursuit = {}

        attempt_count: int = int(pursuit.get("attempt_count", 0)) + 1
        entry = {
            "approach": approach,
            "result": result,
            "result_note": result_note,
            "run_id": ctx.deps.run_id,
        }
        recent: list[object] = list(pursuit.get("recent_attempts", []))
        recent.append(entry)
        if len(recent) > 5:
            recent = recent[-5:]

        pursuit["attempt_count"] = attempt_count
        pursuit["current_approach"] = approach
        pursuit["last_attempt"] = entry
        pursuit["next_action"] = next_action
        pursuit["retryable"] = retryable
        pursuit["recent_attempts"] = recent
        if "max_attempts" not in pursuit:
            pursuit["max_attempts"] = 5

        ctx_data["pursuit"] = pursuit
        repo.update_task(
            task_id,
            context=json.dumps(ctx_data),
            summary=result_note,
            last_agent_run_id=ctx.deps.run_id,
        )

        emit(
            "task.attempt_recorded",
            {
                "task_id": task_id,
                "attempt_count": attempt_count,
                "max_attempts": pursuit["max_attempts"],
                "approach": approach,
                "result": result,
                "retryable": retryable,
                "next_action": next_action,
                "run_id": ctx.deps.run_id,
            },
            run_id=ctx.deps.run_id,
        )

        return (
            f"Attempt {attempt_count} recorded: {result} — {result_note}"
        )

    @agent.tool
    async def schedule_task_followup(
        ctx: RunContext[AgentDeps],
        task_id: str,
        resume_at_iso: str,
        reason: str,
        expected_observation: str,
    ) -> str:
        """Schedule an autonomous follow-up for a task.

        Use this when the task needs to check back autonomously — not waiting
        for the user to reply. The agent will be re-invoked at the scheduled
        time with the reason and expected observation in the prompt.

        Always call record_task_attempt before calling this so the task has
        a record of what was just tried.

        The retry budget is enforced: if attempt_count >= max_attempts the
        follow-up will be rejected — call await_task_input or complete_task
        instead.

        Args:
            task_id: The task ID.
            resume_at_iso: When to resume, as ISO-8601 datetime with timezone.
                          Example: "2026-04-24T14:00:00+02:00"
            reason: Why this follow-up is scheduled (survives into resume prompt).
            expected_observation: What the agent should look for on resume.
        """
        import json as _json
        from datetime import datetime, timedelta, timezone

        from app.control.events import emit
        from app.tasks.repository import TaskRepository
        from app.tasks.service import schedule_task_resume as _schedule

        _MIN_DELAY_SECONDS = 60

        repo = TaskRepository()
        task = repo.get_task(task_id)
        if task is None or task.user_id != ctx.deps.user_id:
            return f"Task {task_id} not found."

        try:
            resume_at = datetime.fromisoformat(resume_at_iso.replace("Z", "+00:00"))
        except ValueError:
            return f"Invalid datetime: {resume_at_iso!r}. Use ISO-8601 with timezone."

        if resume_at.tzinfo is None:
            resume_at = resume_at.replace(tzinfo=timezone.utc)

        now = datetime.now(timezone.utc)
        if resume_at <= now + timedelta(seconds=_MIN_DELAY_SECONDS):
            earliest = (now + timedelta(seconds=_MIN_DELAY_SECONDS)).isoformat()
            return (
                f"Follow-up must be at least {_MIN_DELAY_SECONDS}s in the future."
                f" Earliest: {earliest}"
            )

        # Check retry budget
        try:
            ctx_data: dict[str, object] = _json.loads(task.context or "{}")
        except _json.JSONDecodeError:
            ctx_data = {}

        pursuit = ctx_data.get("pursuit", {})
        if not isinstance(pursuit, dict):
            pursuit = {}

        attempt_count = int(pursuit.get("attempt_count", 0))
        max_attempts = int(pursuit.get("max_attempts", 5))

        if attempt_count >= max_attempts:
            return (
                f"Retry budget exhausted ({attempt_count}/{max_attempts} attempts). "
                "Call await_task_input to ask the user, or complete_task / cancel_task."
            )

        # Store resume intent in pursuit context
        pursuit["resume"] = {
            "reason": reason,
            "expected_observation": expected_observation,
            "resume_at": resume_at_iso,
        }
        ctx_data["pursuit"] = pursuit

        try:
            repo.transition_status(task_id, "AWAITING_RESUME")
        except ValueError as exc:
            return str(exc)

        repo.update_task(
            task_id,
            awaiting_input_hint=reason,
            resume_after=resume_at,
            context=_json.dumps(ctx_data),
            last_agent_run_id=ctx.deps.run_id,
        )

        await _schedule(
            task_id=task_id,
            resume_at=resume_at,
            user_id=ctx.deps.user_id,
            household_id=ctx.deps.household_id,
            channel_user_id=ctx.deps.channel_user_id,
        )

        emit(
            "task.followup_scheduled",
            {
                "task_id": task_id,
                "resume_at": resume_at_iso,
                "reason": reason,
                "expected_observation": expected_observation,
                "attempt_count": attempt_count,
                "max_attempts": max_attempts,
            },
            run_id=ctx.deps.run_id,
        )

        friendly = resume_at.strftime("%A, %d %B %Y at %H:%M")
        return f"Autonomous follow-up scheduled for {friendly}. Reason: {reason}"

    @agent.tool
    async def advance_task_step(
        ctx: RunContext[AgentDeps],
        task_id: str,
        step_index: int,
        status: str,
        result_note: str = "",
        activate_next: bool = True,
    ) -> str:
        """Complete or fail a task step and optionally activate the next one.

        Call this when a step reaches a definitive outcome. Stores result_note
        in the step's details so it survives into the next run's context.

        Args:
            task_id: The task ID.
            step_index: Zero-based index of the step to update.
            status: Outcome — one of "done", "failed", "cancelled".
            result_note: One-sentence summary of what happened at this step.
            activate_next: If True (default) and status is "done", activate
                           the next pending step automatically.
        """
        from datetime import datetime, timezone

        from app.control.events import emit
        from app.tasks.repository import TaskRepository

        valid_statuses = {"done", "failed", "cancelled"}
        if status not in valid_statuses:
            return f"status must be one of: {', '.join(sorted(valid_statuses))}"

        repo = TaskRepository()
        task = repo.get_task(task_id)
        if task is None or task.user_id != ctx.deps.user_id:
            return f"Task {task_id} not found."

        steps = repo.get_steps(task_id)
        step_by_index = {s.step_index: s for s in steps}
        if step_index not in step_by_index:
            available = sorted(step_by_index)
            return f"Step index {step_index} not found. Available: {available}"

        step = step_by_index[step_index]
        now = datetime.now(timezone.utc)

        step_fields: dict[str, object] = {"status": status}
        if result_note:
            step_fields["details_json"] = json.dumps({"result_note": result_note})
        if status == "done":
            step_fields["completed_at"] = now
        elif status in ("failed", "cancelled"):
            step_fields["completed_at"] = now

        repo.update_step(step.id, **step_fields)

        event_type = "task.step_advanced" if status == "done" else "task.step_failed"
        emit(
            event_type,
            {
                "task_id": task_id,
                "step_index": step_index,
                "step_title": step.title,
                "status": status,
                "result_note": result_note,
                "run_id": ctx.deps.run_id,
            },
            run_id=ctx.deps.run_id,
        )

        # Activate the next pending step when requested
        next_activated = ""
        if status == "done" and activate_next:
            next_step = step_by_index.get(step_index + 1)
            if next_step and next_step.status == "pending":
                repo.update_step(next_step.id, status="active", started_at=now)
                repo.update_task(task_id, current_step=step_index + 1)
                next_activated = f" Next step activated: '{next_step.title}'."

        repo.update_task(task_id, last_agent_run_id=ctx.deps.run_id)
        return (
            f"Step {step_index} ('{step.title}') marked {status}."
            + (f" Note: {result_note}" if result_note else "")
            + next_activated
        )

    @agent.tool
    async def fail_task(
        ctx: RunContext[AgentDeps],
        task_id: str,
        reason: str,
        recoverable: bool = False,
        suggested_user_action: str = "",
    ) -> str:
        """Explicitly fail a task when it cannot continue safely or usefully.

        Use this (not cancel_task) when the task has exhausted its options,
        hit an unrecoverable error, or reached a state where continuing
        autonomously would be unsafe or pointless.

        Do NOT use for user-initiated stops — use cancel_task for those.

        Args:
            task_id: The task ID.
            reason: Why the task cannot continue.
            recoverable: True if a user action could unblock it; False if the
                         objective is simply not achievable under current conditions.
            suggested_user_action: Optional hint for what the user could do next.
        """
        from app.control.events import emit
        from app.tasks.repository import TaskRepository

        repo = TaskRepository()
        task = repo.get_task(task_id)
        if task is None or task.user_id != ctx.deps.user_id:
            return f"Task {task_id} not found."

        summary_parts = [f"Failed: {reason}"]
        if recoverable and suggested_user_action:
            summary_parts.append(f"To recover: {suggested_user_action}")
        summary = " ".join(summary_parts)

        try:
            repo.transition_status(task_id, "FAILED")
        except ValueError as exc:
            return str(exc)

        repo.update_task(
            task_id,
            summary=summary,
            last_agent_run_id=ctx.deps.run_id,
        )

        emit(
            "task.fail",
            {
                "task_id": task_id,
                "reason": reason,
                "recoverable": recoverable,
                "suggested_user_action": suggested_user_action,
                "run_id": ctx.deps.run_id,
            },
            run_id=ctx.deps.run_id,
        )

        msg = f"Task failed: {reason}"
        if recoverable and suggested_user_action:
            msg += f" Suggested action: {suggested_user_action}"
        return msg
