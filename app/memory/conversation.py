from __future__ import annotations

import logging
from datetime import datetime, timezone

from pydantic_ai import Agent
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolReturnPart,
    UserPromptPart,
)
from sqlmodel import col, select

from app.db import memory_session
from app.models.memory import ConversationMessage, ConversationSummary, ConversationTurn

logger = logging.getLogger(__name__)

# Keep this many message *pairs* in the rolling window passed to the agent
_MAX_RECENT_PAIRS = 7

# Keep full tool results only for the most recent N turns; strip them from older turns
_FULL_TURNS_KEPT = 2

# Hard character budget for the entire recent-message window.
# Turns are loaded newest-first and dropped once this is exceeded.
# The 2 most recent turns are always kept regardless of size.
_MAX_RECENT_CHARS = 60_000
_MIN_TURNS_KEPT = 2

# Summarize oldest _SUMMARY_BATCH messages once total exceeds _SUMMARY_THRESHOLD
_SUMMARY_THRESHOLD = 20
_SUMMARY_BATCH = 10

_SUMMARIZER_PROMPT = (
    "Summarize the following conversation into concise bullet points. "
    "Capture key topics discussed, decisions made, user preferences revealed, "
    "and any important context. This summary will be injected into future conversations "
    "to provide continuity. Be concise — aim for 4–8 bullet points."
)

_summarizer: Agent[None, str] | None = None


def _strip_tool_results(messages: list[ModelMessage]) -> list[ModelMessage]:
    """Replace ToolReturnPart content with a placeholder to reduce context size.

    Called on older turns where the tool result blobs are no longer useful —
    the LLM only needs to know a tool was called, not what it returned.
    """
    stripped = []
    for msg in messages:
        if not any(isinstance(p, ToolReturnPart) for p in msg.parts):
            stripped.append(msg)
            continue
        new_parts = [
            ToolReturnPart(
                tool_name=p.tool_name,
                content="[result omitted]",
                tool_call_id=p.tool_call_id,
            )
            if isinstance(p, ToolReturnPart)
            else p
            for p in msg.parts
        ]
        stripped.append(msg.model_copy(update={"parts": new_parts}))
    return stripped


def _get_summarizer() -> Agent[None, str]:
    global _summarizer
    if _summarizer is None:
        from app.agent.llm_router import LLMRouter, TaskType

        model = LLMRouter().get_model(TaskType.SUMMARIZATION)
        _summarizer = Agent(model=model, output_type=str, system_prompt=_SUMMARIZER_PROMPT)
    return _summarizer


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


def save_conversation_turn(user_id: str, new_messages: list[ModelMessage]) -> None:
    """Persist the full pydantic-ai message list for one conversation turn.

    Stores tool call exchanges in addition to the final text, so the LLM
    sees the complete history on the next request and does not re-execute
    actions that have already been completed.
    """
    from pydantic_ai.messages import ModelMessagesTypeAdapter

    json_str = ModelMessagesTypeAdapter.dump_json(new_messages).decode()
    with memory_session() as session:
        session.add(
            ConversationTurn(
                user_id=user_id,
                messages_json=json_str,
            )
        )
        session.commit()


def load_recent_messages(
    user_id: str, limit_pairs: int = _MAX_RECENT_PAIRS
) -> list[ModelMessage]:
    """Load the most recent conversation turns as PydanticAI ModelMessage objects.

    Reads from ConversationTurn (full tool-call history). Falls back to
    text-only ConversationMessage rows if no turns exist yet (backward compat).
    """
    from pydantic_ai.messages import ModelMessagesTypeAdapter

    with memory_session() as session:
        turns = session.exec(
            select(ConversationTurn)
            .where(ConversationTurn.user_id == user_id)
            .order_by(col(ConversationTurn.created_at).desc())
            .limit(limit_pairs)
        ).all()

    if turns:
        # turns is newest-first from the query; apply char budget before reversing.
        # Always keep the _MIN_TURNS_KEPT most recent regardless of size.
        selected: list[ConversationTurn] = []
        cumulative_chars = 0
        for i, turn in enumerate(turns):
            size = len(turn.messages_json)
            if i < _MIN_TURNS_KEPT or cumulative_chars + size <= _MAX_RECENT_CHARS:
                selected.append(turn)
                cumulative_chars += size
            else:
                break  # budget exhausted; older turns dropped

        if len(selected) < len(turns):
            logger.debug(
                "Recent message budget: kept %d/%d turns (%d chars)",
                len(selected), len(turns), cumulative_chars,
            )

        # Chronological order; strip tool results from older turns to save context space
        all_turns = list(reversed(selected))
        full_turns = all_turns[-_FULL_TURNS_KEPT:]
        old_turns = all_turns[:-_FULL_TURNS_KEPT]

        messages: list[ModelMessage] = []
        for turn in old_turns:
            try:
                messages.extend(
                    _strip_tool_results(
                        ModelMessagesTypeAdapter.validate_json(turn.messages_json)
                    )
                )
            except Exception:
                logger.warning("Failed to deserialize ConversationTurn id=%s", turn.id)
        for turn in full_turns:
            try:
                messages.extend(ModelMessagesTypeAdapter.validate_json(turn.messages_json))
            except Exception:
                logger.warning("Failed to deserialize ConversationTurn id=%s", turn.id)
        return messages

    # Backward-compat: no turns yet — fall back to text-only pairs
    with memory_session() as session:
        rows = session.exec(
            select(ConversationMessage)
            .where(ConversationMessage.user_id == user_id)
            .order_by(col(ConversationMessage.created_at).desc())
            .limit(limit_pairs * 2)
        ).all()

    ordered = list(reversed(rows))
    fallback: list[ModelMessage] = []
    for row in ordered:
        if row.role == "user":
            fallback.append(ModelRequest(parts=[UserPromptPart(content=row.content)]))
        else:
            fallback.append(ModelResponse(parts=[TextPart(content=row.content)]))
    return fallback


def get_conversation_summary(user_id: str) -> str | None:
    """Return the most recent conversation summary text, or None."""
    with memory_session() as session:
        summary = session.exec(
            select(ConversationSummary).where(ConversationSummary.user_id == user_id)
        ).first()
        return summary.summary if summary else None


async def maybe_summarize_conversation(user_id: str) -> None:
    """
    Background task: summarize and trim old messages when history grows too long.

    When message count exceeds _SUMMARY_THRESHOLD, takes the oldest _SUMMARY_BATCH
    messages, summarizes them (incorporating any existing summary as context), writes
    the result to ConversationSummary, and deletes the summarized messages.

    Silently swallows all errors — this must never crash the caller.
    """
    # Check count without loading all rows
    with memory_session() as session:
        all_ids = session.exec(
            select(ConversationMessage.id)
            .where(ConversationMessage.user_id == user_id)
            .limit(_SUMMARY_THRESHOLD + 1)
        ).all()
        if len(all_ids) <= _SUMMARY_THRESHOLD:
            return

    with memory_session() as session:
        old_msgs = session.exec(
            select(ConversationMessage)
            .where(ConversationMessage.user_id == user_id)
            .order_by(col(ConversationMessage.created_at).asc())
            .limit(_SUMMARY_BATCH)
        ).all()
        if not old_msgs:
            return

    conv_text = "\n".join(f"{m.role.capitalize()}: {m.content}" for m in old_msgs)

    # Include existing summary so the new one is cumulative
    existing_summary: str | None = get_conversation_summary(user_id)
    if existing_summary:
        prompt = (
            f"Existing summary (context from earlier in the conversation):\n"
            f"{existing_summary}\n\n---\n\n"
            f"New conversation to incorporate:\n{conv_text}"
        )
    else:
        prompt = conv_text

    try:
        result = await _get_summarizer().run(prompt)
        summary_text = result.output
    except Exception:
        logger.warning("Conversation summarization failed for user %s", user_id[:8], exc_info=True)
        return

    last_msg_id = old_msgs[-1].id

    with memory_session() as session:
        existing = session.exec(
            select(ConversationSummary).where(ConversationSummary.user_id == user_id)
        ).first()
        if existing:
            existing.summary = summary_text
            existing.covers_through_message_id = last_msg_id
            existing.created_at = datetime.now(timezone.utc)
        else:
            session.add(
                ConversationSummary(
                    user_id=user_id,
                    summary=summary_text,
                    covers_through_message_id=last_msg_id,
                )
            )

        # Remove the summarized messages
        msg_ids = {m.id for m in old_msgs}
        msgs_to_delete = session.exec(
            select(ConversationMessage).where(
                col(ConversationMessage.id).in_(msg_ids)
            )
        ).all()
        for msg in msgs_to_delete:
            session.delete(msg)

        session.commit()

    from app.control.events import emit

    emit(
        "mem.summarize",
        {"messages_compressed": len(old_msgs), "summary": summary_text},
        run_id="",
    )
    logger.info("Summarized %d messages for user %s", len(old_msgs), user_id[:8])
