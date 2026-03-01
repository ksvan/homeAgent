"""
Central message dispatch.

Called by channel handlers when a new user message arrives. Responsible for:
  1. Allowlist gate (belt-and-suspenders; the channel handler also checks)
  2. User DB lookup / first-visit auto-create
  3. Context assembly (profiles, conversation history, memories, device state)
  4. Running the agent and returning the response
  5. Persisting the message pair after a successful run
  6. Updating the device state cache from any Homey tool calls made during the run
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
    if len(_user_call_times[telegram_id]) >= limit_per_minute:
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

        return _UserInfo(
            id=new_user.id,
            name=new_user.name,
            household_id=household.id,
            household_name=household.name,
            is_admin=new_user.is_admin,
        )


async def handle_incoming_message(telegram_id: int, text: str) -> str | None:
    """
    Entry point for all incoming text messages.
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

    from app.agent.agent import run_conversation
    from app.agent.context import assemble_context
    from app.homey.state_cache import update_snapshots_from_tool_calls
    from app.memory.conversation import save_message_pair

    ctx = assemble_context(user.id, user.household_id, text)

    try:
        result = await run_conversation(
            text,
            user_name=user.name,
            household_name=user.household_name,
            message_history=ctx.recent_messages,
            user_profile_text=ctx.user_profile_text,
            household_profile_text=ctx.household_profile_text,
            conversation_summary=ctx.conversation_summary,
            relevant_memories=ctx.relevant_memories,
            home_context_text=ctx.home_context_text,
            user_id=user.id,
            household_id=user.household_id,
            channel_user_id=str(telegram_id),
        )
    except Exception:
        logger.exception("Agent run failed for telegram_id=%d", telegram_id)
        return "Sorry, something went wrong. Please try again in a moment."

    response = str(result.output)

    # Persist messages and update state cache from any Homey tool calls
    save_message_pair(user.id, text, response)
    update_snapshots_from_tool_calls(user.household_id, list(result.new_messages()))

    return response
