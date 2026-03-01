"""
Central message dispatch.

Called by channel handlers when a new user message arrives. Responsible for:
  1. Allowlist gate (belt-and-suspenders; the channel handler also checks)
  2. User DB lookup / first-visit auto-create
  3. Running the agent and returning the response
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlmodel import select

from app.config import get_settings
from app.db import users_session
from app.models.users import Household, User

logger = logging.getLogger(__name__)


@dataclass
class _UserInfo:
    id: str
    name: str
    household_id: str
    is_admin: bool


def _get_or_create_user(telegram_id: int) -> _UserInfo:
    settings = get_settings()
    with users_session() as session:
        user = session.exec(
            select(User).where(User.telegram_id == telegram_id)
        ).first()

        if user:
            return _UserInfo(
                id=user.id,
                name=user.name,
                household_id=user.household_id,
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
            name="User",  # placeholder — M3 onboarding will ask for real name
            is_admin=telegram_id in settings.admin_telegram_ids,
        )
        session.add(new_user)
        session.commit()
        session.refresh(new_user)
        logger.info("New user created (telegram_id=%d)", telegram_id)

        return _UserInfo(
            id=new_user.id,
            name=new_user.name,
            household_id=new_user.household_id,
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

    user = _get_or_create_user(telegram_id)

    from app.agent.agent import run_conversation

    try:
        return await run_conversation(text, user_name=user.name)
    except Exception:
        logger.exception("Agent run failed for telegram_id=%d", telegram_id)
        return "Sorry, something went wrong. Please try again in a moment."
