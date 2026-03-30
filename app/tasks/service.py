"""Task service — context formatting and message resolution."""

from __future__ import annotations

import logging
import re
from datetime import datetime

from app.models.tasks import TERMINAL_STATUSES
from app.tasks.repository import TaskRepository

logger = logging.getLogger(__name__)

# Legacy tasks (reminders/actions) should not appear in agent task context
_LEGACY_KINDS = {None, "legacy"}


def get_active_task_context(user_id: str) -> str:
    """Build the `## Active Task` system prompt section for this user.

    Returns empty string if no non-legacy tasks are active.
    """
    from app.config import get_settings

    if not get_settings().features.multi_step_tasks:
        return ""

    repo = TaskRepository()
    tasks = repo.get_active_tasks(user_id)
    # Filter out legacy (reminder/action) tasks
    tasks = [t for t in tasks if t.task_kind not in _LEGACY_KINDS]

    if not tasks:
        return ""

    if len(tasks) == 1:
        return _render_full_task(repo, tasks[0])

    # Multiple active tasks — render compact list
    lines = ["## Active Tasks"]
    for t in tasks[:5]:  # cap to avoid prompt bloat
        hint = f" — waiting for: {t.awaiting_input_hint}" if t.awaiting_input_hint and t.status == "AWAITING_INPUT" else ""
        lines.append(f"- [{t.task_kind}] {t.title} — {t.status}{hint} (ID: {t.id})")
        if t.summary:
            lines.append(f"  Summary: {t.summary}")
    return "\n".join(lines)


def _render_full_task(repo: TaskRepository, task: object) -> str:
    """Render a single task as a detailed context section."""
    from app.models.tasks import Task

    if not isinstance(task, Task):
        return ""

    lines = ["## Active Task"]
    lines.append(f"- title: {task.title}")
    lines.append(f"- kind: {task.task_kind or 'plan'}")
    lines.append(f"- status: {task.status}")
    if task.summary:
        lines.append(f"- summary: {task.summary}")

    steps = repo.get_steps(task.id)
    if steps:
        current = next((s for s in steps if s.status == "active"), None)
        if current:
            lines.append(f"- current step: {current.title}")

        lines.append("- steps:")
        for s in steps:
            marker = {"done": "[x]", "active": "[>]", "failed": "[!]", "cancelled": "[-]"}.get(s.status, "[ ]")
            lines.append(f"  {marker} {s.title}")

    if task.status == "AWAITING_INPUT" and task.awaiting_input_hint:
        lines.append(f"- waiting for: {task.awaiting_input_hint}")

    # Linked entities
    links = repo.get_links(task.id)
    if links:
        lines.append("- linked entities:")
        for link in links:
            lines.append(f"  - {link.entity_type}: {link.entity_id} ({link.role})")

    lines.append(f"- task ID: {task.id}")
    return "\n".join(lines)


async def schedule_task_resume(
    task_id: str,
    resume_at: datetime,
    user_id: str,
    household_id: str,
    channel_user_id: str,
) -> None:
    """Schedule an APScheduler job to resume a task at a future time."""
    from apscheduler.triggers.date import DateTrigger

    from app.scheduler.engine import get_scheduler
    from app.scheduler.jobs import resume_task

    scheduler = get_scheduler()
    if scheduler is None:
        logger.warning("Scheduler not running — task resume for %s will not fire", task_id)
        return

    schedule_id = f"{task_id}_resume"
    await scheduler.add_schedule(
        resume_task,
        DateTrigger(run_time=resume_at),
        id=schedule_id,
        kwargs={
            "task_id": task_id,
            "user_id": user_id,
            "household_id": household_id,
            "channel_user_id": channel_user_id,
        },
    )
    logger.info("Task resume scheduled: task_id=%s at=%s", task_id, resume_at.isoformat())


def resolve_task_for_message(user_id: str, text: str) -> str | None:
    """Determine which task (if any) an incoming message should attach to.

    Returns a task_id or None.
    """
    from app.config import get_settings

    if not get_settings().features.multi_step_tasks:
        return None

    repo = TaskRepository()
    tasks = repo.get_active_tasks(user_id)
    tasks = [t for t in tasks if t.task_kind not in _LEGACY_KINDS]

    if not tasks:
        return None

    # 1. Explicit task ID reference
    uuid_pattern = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE)
    for match in uuid_pattern.finditer(text):
        candidate = match.group()
        for t in tasks:
            if t.id == candidate:
                return t.id

    # 2. If exactly one task is AWAITING_INPUT, route to it
    awaiting = [t for t in tasks if t.status == "AWAITING_INPUT"]
    if len(awaiting) == 1:
        return awaiting[0].id

    # 3. If exactly one non-legacy task exists, route to it
    if len(tasks) == 1:
        return tasks[0].id

    # Multiple candidates — let the agent disambiguate
    return None
