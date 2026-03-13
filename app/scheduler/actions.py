from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


async def schedule_action(
    user_id: str,
    household_id: str,
    channel_user_id: str,
    description: str,
    tool_name: str,
    tool_args: dict[str, Any],
    run_at: datetime,
) -> str:
    """
    Persist a scheduled Homey action as a Task record and schedule the APScheduler job.

    Returns the task_id which can be used to cancel the action.
    Raises RuntimeError if scheduler registration fails (DB record is rolled back to FAILED).
    """
    from apscheduler.triggers.date import DateTrigger

    from app.db import users_session
    from app.models.tasks import Task
    from app.scheduler.engine import get_scheduler
    from app.scheduler.jobs import execute_homey_action

    if run_at.tzinfo is None:
        run_at = run_at.replace(tzinfo=timezone.utc)

    context_data: dict[str, Any] = {
        "action_tool": tool_name,
        "action_args": tool_args,
        "action_description": description,
        "channel_user_id": channel_user_id,
        "scheduled_at": run_at.isoformat(),
    }
    with users_session() as session:
        task = Task(
            household_id=household_id,
            user_id=user_id,
            title=f"Action: {description[:80]}",
            status="ACTIVE",
            context=json.dumps(context_data),
        )
        session.add(task)
        session.commit()
        session.refresh(task)
        task_id = task.id

    scheduler = get_scheduler()
    if scheduler is not None:
        try:
            await scheduler.add_schedule(
                execute_homey_action,
                DateTrigger(run_time=run_at),
                id=task_id,
                kwargs={
                    "task_id": task_id,
                    "user_id": user_id,
                    "channel_user_id": channel_user_id,
                    "tool_name": tool_name,
                    "tool_args_json": json.dumps(tool_args),
                    "description": description,
                },
            )
        except Exception as exc:
            with users_session() as s:
                t = s.get(Task, task_id)
                if t:
                    t.status = "FAILED"
                    t.completed_at = datetime.now(timezone.utc)
                    s.add(t)
                    s.commit()
            raise RuntimeError(f"Scheduler registration failed: {exc}") from exc
    else:
        logger.warning("Scheduler not running — action task_id=%s will not fire", task_id)

    logger.info(
        "Action scheduled: task_id=%s tool=%s run_at=%s desc=%r",
        task_id,
        tool_name,
        run_at.isoformat(),
        description,
    )
    return task_id


async def restore_pending_actions() -> None:
    """
    Called on startup — reschedule any ACTIVE action tasks whose scheduled
    time is still in the future.  Overdue tasks are skipped (marked FAILED).
    """
    from apscheduler.triggers.date import DateTrigger
    from sqlmodel import select

    from app.db import users_session
    from app.models.tasks import Task
    from app.scheduler.engine import get_scheduler
    from app.scheduler.jobs import execute_homey_action

    scheduler = get_scheduler()
    if scheduler is None:
        return

    now = datetime.now(timezone.utc)
    restored = 0

    with users_session() as session:
        tasks = session.exec(select(Task).where(Task.status == "ACTIVE")).all()

        for task in tasks:
            try:
                ctx = json.loads(task.context)
                if "action_tool" not in ctx:
                    continue  # not an action task

                scheduled_at_str: str | None = ctx.get("scheduled_at")
                channel_user_id: str = ctx.get("channel_user_id", "")
                tool_name: str = ctx.get("action_tool", "")
                tool_args: dict[str, Any] = ctx.get("action_args", {})
                description: str = ctx.get("action_description", task.title)

                if not scheduled_at_str or not tool_name:
                    continue

                try:
                    run_at = datetime.fromisoformat(scheduled_at_str)
                    if run_at.tzinfo is None:
                        run_at = run_at.replace(tzinfo=timezone.utc)
                except ValueError:
                    continue

                if run_at <= now:
                    # Overdue — skip silently; the action time has passed
                    task.status = "FAILED"
                    task.completed_at = now
                    session.add(task)
                    logger.info("Overdue action skipped at restore: task_id=%s", task.id)
                    continue

                await scheduler.add_schedule(
                    execute_homey_action,
                    DateTrigger(run_time=run_at),
                    id=task.id,
                    kwargs={
                        "task_id": task.id,
                        "user_id": task.user_id,
                        "channel_user_id": channel_user_id,
                        "tool_name": tool_name,
                        "tool_args_json": json.dumps(tool_args),
                        "description": description,
                    },
                )
                restored += 1
            except Exception as exc:
                logger.warning(
                    "Skipping action task_id=%s during restore: %s", task.id, exc, exc_info=True
                )

        session.commit()

    if restored:
        logger.info("Restored %d pending action(s) from DB", restored)
