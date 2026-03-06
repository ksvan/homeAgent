from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta

from sqlmodel import select

from app.config import get_settings
from app.db import cache_session
from app.models.cache import PendingAction

logger = logging.getLogger(__name__)


def save_pending_action(
    household_id: str,
    user_id: str,
    tool_name: str,
    tool_args: dict[str, object],
    policy_name: str,
) -> str:
    """
    Persist a PendingAction awaiting user confirmation.

    Returns the UUID token (encoded in the Telegram inline button callback_data).
    """
    settings = get_settings()
    now = datetime.utcnow()
    expires_at = now + timedelta(seconds=settings.telegram_confirm_timeout_seconds)

    with cache_session() as session:
        action = PendingAction(
            household_id=household_id,
            user_id=user_id,
            tool_name=tool_name,
            tool_args=json.dumps(tool_args),
            policy_name=policy_name,
            expires_at=expires_at,
        )
        session.add(action)
        session.commit()
        session.refresh(action)
        return action.token


def get_pending_action(token: str) -> PendingAction | None:
    """Look up a PendingAction by token, returning None if not found or expired."""
    with cache_session() as session:
        action = session.exec(
            select(PendingAction).where(PendingAction.token == token)
        ).first()
        if action is None:
            return None
        if action.expires_at < datetime.utcnow():
            session.delete(action)
            session.commit()
            return None
        # Detach a copy before session closes
        return PendingAction(
            token=action.token,
            household_id=action.household_id,
            user_id=action.user_id,
            tool_name=action.tool_name,
            tool_args=action.tool_args,
            policy_name=action.policy_name,
            created_at=action.created_at,
            expires_at=action.expires_at,
        )


def delete_pending_action(token: str) -> None:
    with cache_session() as session:
        action = session.exec(
            select(PendingAction).where(PendingAction.token == token)
        ).first()
        if action:
            session.delete(action)
            session.commit()
