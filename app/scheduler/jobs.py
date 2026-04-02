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
    emit(
        "job.complete",
        {"job": "reminder", "text": text[:80], "duration_ms": duration_ms, "success": True},
        run_id=task_id,
    )
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
    emit(
        "job.fire",
        {"job": "homey_action", "tool": raw_name, "description": description[:80]},
        run_id=task_id,
    )

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
        {
            "job": "homey_action", "tool": raw_name, "description": description[:80],
            "duration_ms": duration_ms, "success": success,
        },
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


async def resume_task(
    task_id: str,
    user_id: str,
    household_id: str,
    channel_user_id: str,
) -> None:
    """
    APScheduler job — fires when a task's resume_after time arrives.

    Transitions the task back to ACTIVE, runs the agent with full assembled
    context via agent_run(), and delivers the response to the user.
    """
    import uuid as _uuid

    from app.agent.runner import agent_run, get_user_run_lock
    from app.channels.registry import get_channel
    from app.control.events import emit
    from app.db import users_session
    from app.models.tasks import Task

    run_id = str(_uuid.uuid4())
    t0 = _time.monotonic()

    emit("job.fire", {"job": "task_resume", "task_id": task_id}, run_id=run_id)
    logger.info("Task resume starting: task_id=%s run_id=%s", task_id, run_id)

    # Load task and check it's still resumable
    with users_session() as session:
        task = session.get(Task, task_id)
        if task is None or task.status in ("COMPLETED", "FAILED", "CANCELLED"):
            logger.info("Task resume skipped (terminal state): task_id=%s", task_id)
            return

        # Transition back to ACTIVE
        if task.status in ("AWAITING_INPUT", "AWAITING_CONFIRMATION"):
            from datetime import datetime, timezone

            task.status = "ACTIVE"
            task.awaiting_input_hint = None
            task.resume_after = None
            task.updated_at = datetime.now(timezone.utc)
            session.add(task)
            session.commit()

    prompt = (
        f"[Task resume] The scheduled follow-up time has arrived for task {task_id}."
        " Please review the task state and continue or report back to the user."
    )

    # Use the shared per-user lock so this job doesn't race with an incoming
    # user message or another background job for the same user.
    async with get_user_run_lock(user_id):
        outcome = await agent_run(
            text=prompt,
            user_id=user_id,
            household_id=household_id,
            channel_user_id=channel_user_id,
            run_id=run_id,
            trigger="task_resume",
            save_history=True,
        )

    duration_ms = int((_time.monotonic() - t0) * 1000)
    emit(
        "job.complete" if outcome.success else "job.error",
        {
            "job": "task_resume",
            "task_id": task_id,
            "duration_ms": duration_ms,
            "success": outcome.success,
        },
        run_id=run_id,
    )

    channel = get_channel()
    if channel and channel_user_id:
        try:
            await channel.send_message(channel_user_id, outcome.response)
        except Exception:
            logger.warning(
                "Could not deliver task resume response: task_id=%s",
                task_id,
                exc_info=True,
            )


async def fire_scheduled_prompt(
    prompt_id: str,
    user_id: str,
    household_id: str,
    channel_user_id: str,
    prompt_text: str,
    name: str,
    is_one_shot: bool = False,
) -> None:
    """
    APScheduler job — fires on a CronTrigger (recurring) or DateTrigger (once).

    Runs the conversation agent with the stored prompt and sends the response
    to the user via the active channel. One-shot prompts are deleted after firing.
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
            is_one_shot=is_one_shot,
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
    is_one_shot: bool = False,
) -> None:
    import uuid as _uuid
    from datetime import datetime, timezone

    from app.agent.runner import agent_run, get_user_run_lock
    from app.channels.registry import get_channel
    from app.control.events import emit
    from app.db import users_session
    from app.models.scheduled_prompts import ScheduledPrompt, ScheduledPromptLink
    from app.scheduler.delivery import (
        evaluate_postflight,
        evaluate_preflight,
        parse_delivery_policy,
        record_run,
    )
    from app.scheduler.envelope import build_prompt_envelope

    run_id = str(_uuid.uuid4())
    t0 = _time.monotonic()
    fired_at = datetime.now(timezone.utc)

    logger.info(
        "Scheduled prompt starting: prompt_id=%s name=%r run_id=%s", prompt_id, name, run_id
    )

    # --- Load fresh prompt + links from DB (may have updated since registration) ---
    sp: ScheduledPrompt | None = None
    links: list[ScheduledPromptLink] = []
    try:
        from sqlmodel import col, select

        with users_session() as session:
            sp = session.get(ScheduledPrompt, prompt_id)
            if sp:
                links = list(
                    session.exec(
                        select(ScheduledPromptLink).where(
                            col(ScheduledPromptLink.prompt_id) == prompt_id
                        )
                    ).all()
                )
    except Exception:
        logger.error("Failed to load prompt_id=%s from DB", prompt_id, exc_info=True)

    if sp is None or not sp.enabled:
        logger.info("Scheduled prompt skipped (disabled/deleted): prompt_id=%s", prompt_id)
        return

    behavior_kind = sp.behavior_kind or "generic_prompt"

    emit(
        "proactive.fire",
        {"job": "scheduled_prompt", "name": name[:80], "behavior_kind": behavior_kind},
        run_id=run_id,
    )

    # --- Preflight evaluation ---
    try:
        policy = parse_delivery_policy(sp)
        should_proceed, skip_reason = evaluate_preflight(sp, policy, fired_at)
    except Exception:
        logger.warning("Preflight evaluation failed — proceeding", exc_info=True)
        policy = {"skip_if_empty": False, "skip_if_unchanged": False}
        should_proceed, skip_reason = True, None

    if not should_proceed:
        logger.info(
            "Scheduled prompt preflight skip: prompt_id=%s reason=%s", prompt_id, skip_reason
        )
        record_run(prompt_id, run_id, "skipped", skip_reason, None, fired_at)
        duration_ms = int((_time.monotonic() - t0) * 1000)
        emit(
            "proactive.skip",
            {
                "name": name[:80],
                "behavior_kind": behavior_kind,
                "reason": skip_reason,
                "duration_ms": duration_ms,
            },
            run_id=run_id,
        )
        return

    # --- Build prompt envelope ---
    try:
        envelope = build_prompt_envelope(sp, links=links)
    except Exception:
        logger.warning("Envelope build failed — falling back to raw prompt", exc_info=True)
        envelope = prompt_text

    # --- Run the agent with full context assembly ---
    # Use the shared per-user lock so this job doesn't race with an incoming
    # user message or another background job for the same user.
    # save_history=False: proactive outputs don't belong in conversation history.
    async with get_user_run_lock(user_id):
        outcome = await agent_run(
            text=envelope,
            user_id=user_id,
            household_id=household_id,
            channel_user_id=channel_user_id,
            run_id=run_id,
            trigger="scheduled_prompt",
            save_history=False,
        )

    success = outcome.success
    response = outcome.response
    if success:
        logger.info(
            "Scheduled prompt complete: prompt_id=%s name=%r run_id=%s", prompt_id, name, run_id
        )
    else:
        logger.error(
            "Scheduled prompt agent_run failed: prompt_id=%s name=%r", prompt_id, name
        )

    channel = get_channel()

    duration_ms = int((_time.monotonic() - t0) * 1000)
    finished_at = datetime.now(timezone.utc)

    if not success:
        record_run(prompt_id, run_id, "failed", None, response, fired_at, finished_at)
        emit(
            "proactive.fail",
            {"name": name[:80], "behavior_kind": behavior_kind, "duration_ms": duration_ms},
            run_id=run_id,
        )
        # Still deliver the error message to the user
        if channel and channel_user_id:
            try:
                await channel.send_message(channel_user_id, response)
            except Exception:
                logger.error(
                    "Could not deliver scheduled prompt error: prompt_id=%s",
                    prompt_id, exc_info=True,
                )
    else:
        # --- Postflight evaluation ---
        try:
            status, post_skip_reason = evaluate_postflight(response, sp, policy)
        except Exception:
            logger.warning("Postflight evaluation failed — delivering", exc_info=True)
            status, post_skip_reason = "delivered", None

        record_run(prompt_id, run_id, status, post_skip_reason, response, fired_at, finished_at)

        if status == "skipped":
            logger.info(
                "Scheduled prompt postflight skip: prompt_id=%s reason=%s",
                prompt_id, post_skip_reason,
            )
            emit(
                "proactive.skip",
                {
                    "name": name[:80], "behavior_kind": behavior_kind,
                    "reason": post_skip_reason, "duration_ms": duration_ms,
                },
                run_id=run_id,
            )
        else:
            # --- Deliver ---
            if channel and channel_user_id:
                try:
                    await channel.send_message(channel_user_id, response)
                except Exception:
                    logger.error(
                        "Could not deliver scheduled prompt response:"
                        " prompt_id=%s channel_user_id=%s",
                        prompt_id, channel_user_id,
                        exc_info=True,
                    )
            elif not channel:
                logger.error(
                    "Scheduled prompt: no active channel — not delivered:"
                    " prompt_id=%s", prompt_id,
                )

            emit(
                "proactive.deliver",
                {"name": name[:80], "behavior_kind": behavior_kind, "duration_ms": duration_ms},
                run_id=run_id,
            )

    # Emit legacy job event for backward compat with admin UI
    emit(
        "job.complete" if success else "job.error",
        {
            "job": "scheduled_prompt", "name": name[:80],
            "duration_ms": duration_ms, "success": success,
        },
        run_id=run_id,
    )

    # One-shot prompts self-delete after firing (success or failure).
    if is_one_shot:
        try:
            with users_session() as s:
                sp_rec = s.get(ScheduledPrompt, prompt_id)
                if sp_rec:
                    s.delete(sp_rec)
                    s.commit()
            logger.info(
                "One-shot prompt deleted after firing: prompt_id=%s name=%r",
                prompt_id, name,
            )
        except Exception:
            logger.warning(
                "Failed to delete one-shot prompt after firing: prompt_id=%s",
                prompt_id, exc_info=True,
            )
