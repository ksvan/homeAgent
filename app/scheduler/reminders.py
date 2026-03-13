from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


async def schedule_reminder(
    user_id: str,
    household_id: str,
    channel_user_id: str,
    text: str,
    run_at: datetime,
) -> str:
    """
    Persist a reminder as a Task record and schedule the APScheduler job.

    Returns the task_id which can be used to cancel the reminder.
    Raises RuntimeError if scheduler registration fails (DB record is rolled back to FAILED).
    """
    from apscheduler.triggers.date import DateTrigger

    from app.db import users_session
    from app.models.tasks import Task
    from app.scheduler.engine import get_scheduler
    from app.scheduler.jobs import send_reminder

    # Normalise to UTC-aware
    if run_at.tzinfo is None:
        run_at = run_at.replace(tzinfo=timezone.utc)

    # Persist to DB first so we have the task_id
    context_data: dict[str, Any] = {
        "reminder_text": text,
        "channel_user_id": channel_user_id,
        "scheduled_at": run_at.isoformat(),
    }
    with users_session() as session:
        task = Task(
            household_id=household_id,
            user_id=user_id,
            title=f"Reminder: {text[:80]}",
            status="ACTIVE",
            context=json.dumps(context_data),
        )
        session.add(task)
        session.commit()
        session.refresh(task)
        task_id = task.id

    # Schedule via APScheduler — use task_id as the schedule id for easy lookup
    scheduler = get_scheduler()
    if scheduler is not None:
        try:
            await scheduler.add_schedule(
                send_reminder,
                DateTrigger(run_time=run_at),
                id=task_id,
                kwargs={
                    "task_id": task_id,
                    "user_id": user_id,
                    "channel_user_id": channel_user_id,
                    "text": text,
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
        logger.warning("Scheduler not running — reminder task_id=%s will not fire", task_id)

    logger.info(
        "Reminder scheduled: task_id=%s run_at=%s text=%r", task_id, run_at.isoformat(), text
    )
    return task_id


def cancel_reminder(task_id: str) -> bool:
    """
    Cancel a pending reminder by task_id.

    Returns True if the reminder was found and cancelled, False otherwise.
    """
    from app.db import users_session
    from app.models.tasks import Task
    from app.scheduler.engine import get_scheduler

    with users_session() as session:
        task = session.get(Task, task_id)
        if task is None or task.status != "ACTIVE":
            return False

        task.status = "CANCELLED"
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
                logger.debug(
                    "Could not remove schedule %s (may have already fired)", task_id
                )

        asyncio.ensure_future(_remove())

    logger.info("Reminder cancelled: task_id=%s", task_id)
    return True


async def restore_pending_reminders() -> None:
    """
    Called on startup — reschedule any ACTIVE reminder tasks whose scheduled
    time is still in the future.  Tasks already overdue are fired immediately.
    """
    from apscheduler.triggers.date import DateTrigger
    from sqlmodel import select

    from app.db import users_session
    from app.models.tasks import Task
    from app.scheduler.engine import get_scheduler
    from app.scheduler.jobs import send_reminder

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
                scheduled_at_str: str | None = ctx.get("scheduled_at")
                channel_user_id: str = ctx.get("channel_user_id", "")
                reminder_text: str = ctx.get("reminder_text", task.title)

                if not scheduled_at_str or not channel_user_id:
                    continue  # not a reminder task

                try:
                    run_at = datetime.fromisoformat(scheduled_at_str)
                    if run_at.tzinfo is None:
                        run_at = run_at.replace(tzinfo=timezone.utc)
                except ValueError:
                    continue

                # Overdue: fire at the next scheduler tick (~1 s from now)
                fire_at = run_at if run_at > now else now.replace(microsecond=0)

                await scheduler.add_schedule(
                    send_reminder,
                    DateTrigger(run_time=fire_at),
                    id=task.id,
                    kwargs={
                        "task_id": task.id,
                        "user_id": task.user_id,
                        "channel_user_id": channel_user_id,
                        "text": reminder_text,
                    },
                )
                restored += 1
            except Exception as exc:
                logger.warning(
                    "Skipping reminder task_id=%s during restore: %s", task.id, exc, exc_info=True
                )

    if restored:
        logger.info("Restored %d pending reminder(s) from DB", restored)
