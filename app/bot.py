"""
Central message dispatch.

Called by channel handlers when a new user message arrives. Responsible for:
  1. Allowlist gate (belt-and-suspenders; the channel handler also checks)
  2. User DB lookup / first-visit auto-create
  3. Running the agent via agent_run() (context assembly, execution, logging)
  4. Persisting the text-only message pair for summarization
  5. Updating the device state cache from any Homey tool calls made during the run
"""
from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from time import monotonic

from sqlmodel import select

from app.config import get_settings
from app.db import users_session
from app.models.users import Household, User

logger = logging.getLogger(__name__)

# Per-user sliding-window rate limiter (in-memory; resets on restart)
_user_call_times: dict[int, list[float]] = defaultdict(list)


def _is_rate_limited(telegram_id: int, limit_per_minute: int) -> bool:
    """Return True if the user has exceeded limit_per_minute calls in 60 s."""
    now = monotonic()
    calls = _user_call_times[telegram_id]
    _user_call_times[telegram_id] = [t for t in calls if now - t < 60.0]
    if not _user_call_times[telegram_id]:
        del _user_call_times[telegram_id]
    if len(_user_call_times.get(telegram_id, [])) >= limit_per_minute:
        return True
    _user_call_times[telegram_id].append(now)
    return False


@dataclass
class _UserInfo:
    id: str
    name: str
    household_id: str
    household_name: str
    is_admin: bool


def _get_or_create_user(telegram_id: int) -> _UserInfo:
    settings = get_settings()
    with users_session() as session:
        user = session.exec(
            select(User).where(User.telegram_id == telegram_id)
        ).first()

        if user:
            household = session.exec(
                select(Household).where(Household.id == user.household_id)
            ).first()
            household_name = household.name if household else "the household"
            return _UserInfo(
                id=user.id,
                name=user.name,
                household_id=user.household_id,
                household_name=household_name,
                is_admin=user.is_admin,
            )

        # First visit — create a household if none exists yet
        household = session.exec(select(Household)).first()
        if not household:
            household = Household(name="My Home")
            session.add(household)
            session.flush()

        new_user = User(
            household_id=household.id,
            telegram_id=telegram_id,
            name="User",  # placeholder — agent will ask for real name
            is_admin=telegram_id in settings.admin_telegram_ids,
        )
        session.add(new_user)
        session.commit()
        session.refresh(new_user)
        logger.info("New user created (telegram_id=%d)", telegram_id)

        # Immediately link the new user to a HouseholdMember so the world
        # model can mark them as the current speaker without waiting for
        # the next startup bootstrap.
        from app.world.repository import WorldModelRepository
        WorldModelRepository.upsert_member(
            household.id,
            user_id=new_user.id,
            name=new_user.name,
            role="admin" if new_user.is_admin else "member",
            source="migration_seed",
        )

        return _UserInfo(
            id=new_user.id,
            name=new_user.name,
            household_id=household.id,
            household_name=household.name,
            is_admin=new_user.is_admin,
        )


async def handle_incoming_message(
    telegram_id: int, text: str, attachments: list | None = None
) -> str | None:
    """
    Entry point for all incoming messages (text and/or media).
    Returns the response string to send back, or None to send nothing.
    """
    settings = get_settings()

    if telegram_id not in settings.allowed_telegram_ids:
        return None  # silent drop

    if not (settings.is_development or settings.is_test) and _is_rate_limited(
        telegram_id, settings.rate_limit_per_user_per_minute
    ):
        logger.warning("Rate limit exceeded for telegram_id=%d — dropping message", telegram_id)
        return "You're sending messages too quickly. Please wait a moment before trying again."

    user = _get_or_create_user(telegram_id)

    if text.startswith("/"):
        from app.commands.dispatcher import try_dispatch
        cmd_response = await try_dispatch(
            text,
            user_id=user.id,
            user_name=user.name,
            telegram_id=telegram_id,
            is_admin=user.is_admin,
            household_id=user.household_id,
        )
        if cmd_response is not None:
            return cmd_response

    from app.agent.runner import agent_run, get_user_run_lock
    from app.channels.registry import get_channel
    from app.homey.state_cache import update_snapshots_from_tool_calls
    from app.memory.conversation import save_message_pair

    channel_user_id = str(telegram_id)

    # Serialize per-user: Q2 waits for Q1 to finish so responses never arrive
    # out of order and context always sees the latest saved history.
    # get_user_run_lock is keyed by user_id and shared with background jobs.
    async with get_user_run_lock(user.id):
        _media_list = attachments or []

        async def _on_retry(attempt: int) -> None:
            if attempt == 0:
                ch = get_channel()
                if ch:
                    try:
                        await ch.send_message(
                            channel_user_id, "One moment — retrying shortly."
                        )
                    except Exception:
                        pass

        outcome = await agent_run(
            text=text,
            user_id=user.id,
            household_id=user.household_id,
            channel_user_id=channel_user_id,
            trigger="user_message",
            user_name=user.name,
            household_name=user.household_name,
            media=_media_list,
            save_history=True,
            retries=2,
            on_retry=_on_retry,
        )

        if not outcome.success:
            return outcome.response

        # Persist text-only message pair for summarization.
        # Build a text label that captures media types when there is no text.
        if not text and _media_list:
            media_label = ", ".join(f"[{a.mime_type.split('/')[0]}]" for a in _media_list)
        else:
            media_label = text
        save_message_pair(user.id, media_label, outcome.response)

        # Update Homey device state cache from tool calls made during this run.
        update_snapshots_from_tool_calls(user.household_id, outcome.new_messages)

        return outcome.response


