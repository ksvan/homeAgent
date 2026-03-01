from __future__ import annotations

import logging
from datetime import datetime, timezone

from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    UserPromptPart,
)
from sqlmodel import col, select

from app.db import memory_session
from app.models.memory import ConversationMessage, ConversationSummary

logger = logging.getLogger(__name__)

# Keep this many message *pairs* in the rolling window passed to the agent
_MAX_RECENT_PAIRS = 20


def save_message_pair(user_id: str, user_text: str, assistant_text: str) -> None:
    """Persist a user/assistant message pair to the conversation history."""
    now = datetime.now(timezone.utc)
    with memory_session() as session:
        session.add(
            ConversationMessage(user_id=user_id, role="user", content=user_text, created_at=now)
        )
        session.add(
            ConversationMessage(
                user_id=user_id, role="assistant", content=assistant_text, created_at=now
            )
        )
        session.commit()


def load_recent_messages(
    user_id: str, limit_pairs: int = _MAX_RECENT_PAIRS
) -> list[ModelMessage]:
    """Load the most recent messages and return them as PydanticAI ModelMessage objects."""
    with memory_session() as session:
        rows = session.exec(
            select(ConversationMessage)
            .where(ConversationMessage.user_id == user_id)
            .order_by(col(ConversationMessage.created_at).desc())
            .limit(limit_pairs * 2)
        ).all()

    # Reverse to chronological order
    ordered = list(reversed(rows))

    messages: list[ModelMessage] = []
    for row in ordered:
        if row.role == "user":
            messages.append(
                ModelRequest(parts=[UserPromptPart(content=row.content)])
            )
        else:
            messages.append(
                ModelResponse(parts=[TextPart(content=row.content)])
            )
    return messages


def get_conversation_summary(user_id: str) -> str | None:
    """Return the most recent conversation summary text, or None."""
    with memory_session() as session:
        summary = session.exec(
            select(ConversationSummary).where(ConversationSummary.user_id == user_id)
        ).first()
        return summary.summary if summary else None
