from __future__ import annotations

import logging
import time as _time

logger = logging.getLogger(__name__)

# In-flight guard: prevents a second run from starting if the first hasn't finished.
_running_prompts: set[str] = set()


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
    from app.control.events import emit
    from app.db import users_session
    from app.models.tasks import Task

    t0 = _time.monotonic()
    emit("job.fire", {"job": "reminder", "text": text[:80]}, run_id=task_id)

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

    duration_ms = int((_time.monotonic() - t0) * 1000)
    emit("job.complete", {"job": "reminder", "text": text[:80], "duration_ms": duration_ms, "success": True}, run_id=task_id)
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
    from app.control.events import emit
    from app.db import users_session
    from app.homey.mcp_client import get_mcp_server
    from app.models.tasks import Task

    channel = get_channel()
    server = get_mcp_server()
    tool_args: dict[str, object] = json.loads(tool_args_json)

    # Strip the homey_ prefix — direct_call_tool uses the raw MCP tool name
    raw_name = tool_name.removeprefix("homey_")

    t0 = _time.monotonic()
    emit("job.fire", {"job": "homey_action", "tool": raw_name, "description": description[:80]}, run_id=task_id)

    success = False
    if server is None:
        msg = f"⚠️ Could not run scheduled action — Homey is not connected.\nAction: {description}"
        logger.warning("Scheduled action skipped (MCP not connected): task_id=%s", task_id)
    else:
        # Policy gate: enforce confirmation requirements at execution time too.
        # Scheduled actions cannot pause for interactive confirmation, so block them.
        from app.policy.gate import evaluate_policy

        decision = evaluate_policy(raw_name, tool_args)
        if decision.requires_confirm:
            msg = (
                f"⛔ Scheduled action blocked: '{description}' requires confirmation "
                "and cannot run unattended. Use the chat to run it directly."
            )
            logger.warning(
                "Scheduled action blocked by policy: task_id=%s tool=%s policy=%s",
                task_id, raw_name, decision.policy_name,
            )
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

    duration_ms = int((_time.monotonic() - t0) * 1000)
    emit(
        "job.complete" if success else "job.error",
        {"job": "homey_action", "tool": raw_name, "description": description[:80], "duration_ms": duration_ms, "success": success},
        run_id=task_id,
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


async def fire_scheduled_prompt(
    prompt_id: str,
    user_id: str,
    household_id: str,
    channel_user_id: str,
    prompt_text: str,
    name: str,
) -> None:
    """
    APScheduler job — fires on a recurring CronTrigger for a ScheduledPrompt.

    Runs the conversation agent with the stored prompt and sends the response
    to the user via the active channel.
    """
    if prompt_id in _running_prompts:
        logger.warning(
            "Scheduled prompt prompt_id=%s already in-flight — skipping overlap", prompt_id
        )
        return

    _running_prompts.add(prompt_id)
    try:
        await _fire_scheduled_prompt_inner(
            prompt_id=prompt_id,
            user_id=user_id,
            household_id=household_id,
            channel_user_id=channel_user_id,
            prompt_text=prompt_text,
            name=name,
        )
    finally:
        _running_prompts.discard(prompt_id)


async def _fire_scheduled_prompt_inner(
    prompt_id: str,
    user_id: str,
    household_id: str,
    channel_user_id: str,
    prompt_text: str,
    name: str,
) -> None:
    import uuid as _uuid

    from app.agent.agent import run_conversation
    from app.channels.registry import get_channel
    from app.control.events import emit
    from app.db import users_session
    from app.models.users import Household, User

    run_id = str(_uuid.uuid4())
    t0 = _time.monotonic()
    emit(
        "job.fire",
        {"job": "scheduled_prompt", "name": name[:80], "prompt": prompt_text[:80]},
        run_id=run_id,
    )

    # Resolve display names from DB
    user_name = "user"
    household_name = "the household"
    with users_session() as session:
        user = session.get(User, user_id)
        household = session.get(Household, household_id)
        if user:
            user_name = user.name
        if household:
            household_name = household.name

    channel = get_channel()
    success = False
    try:
        result = await run_conversation(
            text=prompt_text,
            user_name=user_name,
            household_name=household_name,
            user_id=user_id,
            household_id=household_id,
            channel_user_id=channel_user_id,
            run_id=run_id,
        )
        response = result.output
        success = True
        logger.info(
            "Scheduled prompt fired: prompt_id=%s name=%r run_id=%s", prompt_id, name, run_id
        )
    except Exception:
        response = f"Scheduled prompt '{name}' failed to run."
        logger.warning(
            "Scheduled prompt failed: prompt_id=%s name=%r", prompt_id, name, exc_info=True
        )

    duration_ms = int((_time.monotonic() - t0) * 1000)
    emit(
        "job.complete" if success else "job.error",
        {
            "job": "scheduled_prompt",
            "name": name[:80],
            "duration_ms": duration_ms,
            "success": success,
        },
        run_id=run_id,
    )

    if channel and channel_user_id:
        try:
            await channel.send_message(channel_user_id, response)
        except Exception:
            logger.warning(
                "Could not deliver scheduled prompt response to channel_user_id=%s",
                channel_user_id,
                exc_info=True,
            )
