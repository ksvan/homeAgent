"""Startup restore for AWAITING_RESUME tasks.

Reschedules autonomous task follow-ups whose resume_after is still in the future.
Overdue tasks (resume_after in the past) are handled by firing them near-immediately,
unless they exceed the stale threshold — in which case task.resume_missed is emitted
and the task is transitioned back to ACTIVE for the agent to handle on next contact.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

# If a resume is more than this many minutes overdue, treat it as missed rather
# than firing it immediately (avoids surprising the user with a very stale run).
_STALE_THRESHOLD_MINUTES = 60


async def restore_pending_task_resumes() -> None:
    """Called on startup — reschedule AWAITING_RESUME tasks with a future resume_after.

    For overdue-but-recent tasks: fire shortly after startup.
    For stale tasks (overdue > threshold): emit task.resume_missed and reset to ACTIVE.
    """
    from apscheduler.triggers.date import DateTrigger
    from sqlmodel import select

    from app.control.events import emit
    from app.db import users_session
    from app.models.tasks import Task
    from app.models.users import ChannelMapping
    from app.scheduler.engine import get_scheduler
    from app.scheduler.jobs import resume_task

    scheduler = get_scheduler()
    if scheduler is None:
        return

    now = datetime.now(timezone.utc)
    stale_cutoff = now - timedelta(minutes=_STALE_THRESHOLD_MINUTES)
    restored = 0
    missed = 0

    with users_session() as session:
        tasks = session.exec(
            select(Task).where(Task.status == "AWAITING_RESUME")
        ).all()

        for task in tasks:
            if task.resume_after is None:
                # No scheduled time stored — reset to ACTIVE so agent can decide
                task.status = "ACTIVE"
                task.updated_at = now
                session.add(task)
                logger.warning(
                    "Task AWAITING_RESUME has no resume_after, resetting to ACTIVE: task_id=%s",
                    task.id,
                )
                continue

            resume_at = task.resume_after
            if resume_at.tzinfo is None:
                resume_at = resume_at.replace(tzinfo=timezone.utc)

            # Resolve channel_user_id from channel_mapping
            mapping = session.exec(
                select(ChannelMapping).where(ChannelMapping.user_id == task.user_id)
            ).first()
            channel_user_id = mapping.channel_user_id if mapping else task.user_id

            if resume_at > now:
                # Still in the future — reschedule normally
                schedule_id = f"task:{task.id}:resume"
                await scheduler.add_schedule(
                    resume_task,
                    DateTrigger(run_time=resume_at),
                    id=schedule_id,
                    kwargs={
                        "task_id": task.id,
                        "user_id": task.user_id,
                        "household_id": task.household_id,
                        "channel_user_id": channel_user_id,
                    },
                )
                restored += 1
                logger.info(
                    "Task resume restored: task_id=%s resume_at=%s",
                    task.id,
                    resume_at.isoformat(),
                )
            elif resume_at >= stale_cutoff:
                # Overdue but recent — fire shortly after startup
                fire_at = now + timedelta(seconds=10)
                schedule_id = f"task:{task.id}:resume"
                await scheduler.add_schedule(
                    resume_task,
                    DateTrigger(run_time=fire_at),
                    id=schedule_id,
                    kwargs={
                        "task_id": task.id,
                        "user_id": task.user_id,
                        "household_id": task.household_id,
                        "channel_user_id": channel_user_id,
                    },
                )
                restored += 1
                logger.info(
                    "Overdue task resume re-queued for near-immediate fire: task_id=%s was_due=%s",
                    task.id,
                    resume_at.isoformat(),
                )
            else:
                # Stale — too old to fire; reset to ACTIVE and notify
                task.status = "ACTIVE"
                task.resume_after = None
                task.updated_at = now
                session.add(task)
                missed += 1
                emit(
                    "task.resume_missed",
                    {
                        "task_id": task.id,
                        "was_due": resume_at.isoformat(),
                        "overdue_minutes": int((now - resume_at).total_seconds() / 60),
                    },
                )
                logger.warning(
                    "Stale task resume missed: task_id=%s was_due=%s overdue_minutes=%d",
                    task.id,
                    resume_at.isoformat(),
                    int((now - resume_at).total_seconds() / 60),
                )

        session.commit()

    if restored or missed:
        logger.info(
            "Task resume restore complete: restored=%d missed=%d", restored, missed
        )
