"""
Web chat inbound message dispatch.

Mirrors app.bot.handle_incoming_message's shape (rate limit -> slash
command routing -> agent_run -> save_message_pair) but for web sessions:
no auto-create-on-first-message (a web session always names an existing
User already picked from the login screen), and channel_user_id is the
session token rather than a telegram_id.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid as _uuid
from collections.abc import Awaitable, Callable

from sqlmodel import select

from app.bot import _is_rate_limited
from app.config import get_settings
from app.db import users_session
from app.models.users import Household, User
from app.policy.confirm import ConfirmResult, cancel_pending_action_for_user, execute_pending_action
from app.webchat.session import SessionInfo

logger = logging.getLogger(__name__)

StatusCallback = Callable[[str], Awaitable[None]]


def _resolve_names(user_id: str, household_id: str) -> tuple[str, str]:
    with users_session() as db:
        user = db.exec(select(User).where(User.id == user_id)).first()
        household = db.exec(select(Household).where(Household.id == household_id)).first()
    return (user.name if user else "", household.name if household else "the household")


async def _forward_status(run_id: str, on_status: StatusCallback) -> None:
    """Forward this run's tool-call events as human-readable status lines
    until the run completes. Reuses the same control-event bus the admin
    dashboard's SSE stream subscribes to (app.control.events) — no new
    plumbing, just a run_id-filtered consumer."""
    from app.control.events import subscribe, unsubscribe

    q = subscribe()
    try:
        await on_status("thinking…")
        while True:
            event = await q.get()
            if event.run_id != run_id:
                continue
            if event.event_type == "run.tool_call":
                tool = event.payload.get("tool", "a tool")
                await on_status(f"using {tool}…")
            elif event.event_type in ("run.complete", "run.error"):
                return
    finally:
        unsubscribe(q)


async def _run_with_status(
    *,
    text: str,
    session: SessionInfo,
    user_name: str,
    household_name: str,
    on_status: StatusCallback | None,
) -> object:
    from app.agent.runner import agent_run

    run_id = str(_uuid.uuid4())

    forward_task: asyncio.Task[None] | None = None
    if on_status is not None:
        forward_task = asyncio.ensure_future(_forward_status(run_id, on_status))

    try:
        return await agent_run(
            text=text,
            user_id=session.user_id,
            household_id=session.household_id,
            channel_user_id=session.token,
            channel="web",
            run_id=run_id,
            trigger="user_message",
            user_name=user_name,
            household_name=household_name,
            save_history=True,
            retries=1,
        )
    finally:
        if forward_task is not None:
            forward_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await forward_task


async def handle_web_message(
    session: SessionInfo,
    text: str,
    on_status: StatusCallback | None = None,
) -> str | None:
    """Process one inbound web chat message. Returns the response text.

    on_status, if given, is awaited with human-readable progress labels
    ("using set_light…") while the agent run is in flight — drives the
    browser's tool-in-progress indicator.
    """
    from app.agent.runner import RunOutcome, get_user_run_lock

    settings = get_settings()

    if not (settings.is_development or settings.is_test) and _is_rate_limited(
        session.user_id, settings.rate_limit_per_user_per_minute
    ):
        logger.warning("Rate limit exceeded for web user_id=%s — dropping message", session.user_id)
        return "You're sending messages too quickly. Please wait a moment before trying again."

    user_name, household_name = _resolve_names(session.user_id, session.household_id)

    if text.startswith("/"):
        from app.commands.dispatcher import try_dispatch

        cmd_response = await try_dispatch(
            text,
            user_id=session.user_id,
            user_name=user_name,
            telegram_id=0,
            is_admin=False,
            household_id=session.household_id,
        )
        if cmd_response is not None:
            return cmd_response

    async with get_user_run_lock(session.user_id):
        outcome = await _run_with_status(
            text=text,
            session=session,
            user_name=user_name,
            household_name=household_name,
            on_status=on_status,
        )
        assert isinstance(outcome, RunOutcome)

        if not outcome.success:
            return outcome.response

        from app.homey.state_cache import update_snapshots_from_tool_calls
        from app.memory.conversation import save_message_pair

        save_message_pair(session.user_id, text, outcome.response)
        update_snapshots_from_tool_calls(session.household_id, outcome.new_messages)

        return outcome.response


async def handle_web_confirm(session: SessionInfo, token: str) -> ConfirmResult:
    return await execute_pending_action(token, session.user_id, session.token, channel="web")


async def handle_web_cancel(session: SessionInfo, token: str) -> ConfirmResult:
    return await cancel_pending_action_for_user(token, session.user_id)
