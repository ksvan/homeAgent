"""
Auto-memory extraction.

After each agent run, this module analyses the new conversation exchange with a
cheap background model and stores any stable facts it finds as episodic memories.
Called as a fire-and-forget coroutine from bot.py — never blocks the response.
"""
from __future__ import annotations

import logging

from pydantic import BaseModel
from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessage, TextPart, UserPromptPart

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
You are a memory extraction assistant for a household AI.

Review the conversation and extract stable, durable facts worth remembering across sessions.

EXTRACT only:
- User preferences and habits (communication style, schedules, routines)
- Household facts (device purposes, room layouts, named entities, permanent configuration)
- Recurring patterns the assistant should remember in future conversations

DO NOT extract:
- Current device states or sensor readings (these are fetched live)
- Current date, time, weather, or any ephemeral information
- Temporary situations mentioned in passing
- Greetings, thanks, or conversational filler
- Anything that will likely be wrong or irrelevant tomorrow

Return an empty facts list if nothing qualifies.
Write each fact as a complete, standalone sentence.

For each fact also set an importance level:
- "critical"  — safety/medical facts, permanent household config that must never be forgotten
- "important" — strong recurring preferences, long-term patterns worth keeping for at least a year
- "normal"    — general observations or situational preferences (default)
- "ephemeral" — short-lived context unlikely to matter beyond the next few weeks
"""


class _Fact(BaseModel):
    content: str
    importance: str = "normal"


class _Facts(BaseModel):
    facts: list[_Fact]


_extractor: Agent[None, _Facts] | None = None


def _get_extractor() -> Agent[None, _Facts]:
    global _extractor
    if _extractor is None:
        from app.agent.llm_router import LLMRouter, TaskType

        model = LLMRouter().get_model(TaskType.MEMORY_EXTRACTION)
        _extractor = Agent(model=model, output_type=_Facts, system_prompt=_SYSTEM_PROMPT)
    return _extractor


def _messages_to_text(messages: list[ModelMessage]) -> str:
    """Convert pydantic-ai messages to plain text, keeping only user/assistant turns."""
    lines: list[str] = []
    for msg in messages:
        for part in msg.parts:
            if isinstance(part, UserPromptPart):
                lines.append(f"User: {part.content}")
            elif isinstance(part, TextPart):
                lines.append(f"Assistant: {part.content}")
    return "\n".join(lines)


async def extract_and_store_memories(
    household_id: str,
    user_id: str,
    run_id: str,
    new_messages: list[ModelMessage],
) -> None:
    """
    Background task: extract stable facts from a conversation run and persist them.

    Silently swallows all errors — this must never crash the caller.
    """
    if not new_messages:
        return

    text = _messages_to_text(new_messages)
    if not text.strip():
        return

    try:
        result = await _get_extractor().run(text)
        facts = result.output.facts
    except Exception:
        logger.warning("Memory extraction failed for run %s", run_id[:8], exc_info=True)
        return

    if not facts:
        return

    from app.control.events import emit
    from app.memory.episodic import store_memory

    stored_facts: list[str] = []
    for fact in facts:
        content = fact.content.strip()
        importance = fact.importance if fact.importance in {
            "critical", "important", "normal", "ephemeral"
        } else "normal"
        if not content:
            continue
        try:
            store_memory(
                household_id=household_id,
                content=content,
                source_run_id=run_id,
                importance=importance,
            )
            stored_facts.append(content)
        except Exception:
            logger.warning("Failed to auto-store memory: %.80s", content, exc_info=True)

    if stored_facts:
        emit(
            "mem.extract",
            {"facts": stored_facts},
            run_id=run_id,
        )
        logger.info(
            "Auto-extracted %d memor%s from run %s",
            len(stored_facts),
            "ies" if len(stored_facts) != 1 else "y",
            run_id[:8],
        )
