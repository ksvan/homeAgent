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


async def execute_homey_action(
    task_id: str,
    user_id: str,
    channel_user_id: str,
    tool_name: str,
    tool_args_json: str,
    description: str,
) -> None:
    """
    APScheduler job — fires when a scheduled Homey action is due.

    Calls the Homey MCP tool directly, notifies the user of success or failure,
    and marks the Task as COMPLETED.
    """
    import json

    from app.channels.registry import get_channel
    from app.db import users_session
    from app.homey.mcp_client import get_mcp_server
    from app.models.tasks import Task

    channel = get_channel()
    server = get_mcp_server()
    tool_args: dict[str, object] = json.loads(tool_args_json)

    # Strip the homey_ prefix — direct_call_tool uses the raw MCP tool name
    raw_name = tool_name.removeprefix("homey_")

    success = False
    if server is None:
        msg = f"⚠️ Could not run scheduled action — Homey is not connected.\nAction: {description}"
        logger.warning("Scheduled action skipped (MCP not connected): task_id=%s", task_id)
    else:
        try:
            await server.direct_call_tool(raw_name, tool_args)
            msg = f"✅ Done: {description}"
            success = True
            logger.info("Scheduled action executed: task_id=%s tool=%s", task_id, raw_name)
        except Exception:
            msg = f"❌ Scheduled action failed: {description}"
            logger.warning(
                "Scheduled action failed: task_id=%s tool=%s", task_id, raw_name, exc_info=True
            )

    if channel and channel_user_id:
        try:
            await channel.send_message(channel_user_id, msg)
        except Exception:
            logger.warning("Could not notify user of scheduled action result", exc_info=True)

    # Mark the task completed regardless of outcome
    with users_session() as session:
        task = session.get(Task, task_id)
        if task and task.status == "ACTIVE":
            from datetime import datetime, timezone

            task.status = "COMPLETED" if success else "FAILED"
            task.completed_at = datetime.now(timezone.utc)
            session.add(task)
            session.commit()
