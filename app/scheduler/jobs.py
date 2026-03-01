from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def send_reminder(
    task_id: str,
    user_id: str,
    channel_user_id: str,
    text: str,
) -> None:
    """
    APScheduler job — fires when a reminder is due.

    Sends the reminder message to the user via the active channel and marks
    the corresponding Task as COMPLETED.
    """
    from app.channels.registry import get_channel
    from app.db import users_session
    from app.models.tasks import Task

    # Send the message
    channel = get_channel()
    if channel and channel_user_id:
        try:
            await channel.send_message(channel_user_id, f"⏰ Reminder: {text}")
        except Exception:
            logger.warning(
                "Could not send reminder to channel_user_id=%s", channel_user_id, exc_info=True
            )

    # Mark the task completed
    with users_session() as session:
        task = session.get(Task, task_id)
        if task and task.status == "ACTIVE":
            task.status = "COMPLETED"
            from datetime import datetime, timezone

            task.completed_at = datetime.now(timezone.utc)
            session.add(task)
            session.commit()

    logger.info("Reminder fired: task_id=%s user_id=%s", task_id, user_id)
