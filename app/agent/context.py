from __future__ import annotations

import logging
from dataclasses import dataclass, field

from pydantic_ai.messages import ModelMessage

from app.memory.conversation import get_conversation_summary, load_recent_messages
from app.memory.episodic import search_memories
from app.memory.profiles import format_profile, get_household_profile, get_user_profile

logger = logging.getLogger(__name__)


@dataclass
class AgentContext:
    recent_messages: list[ModelMessage] = field(default_factory=list)
    user_profile_text: str = ""
    household_profile_text: str = ""
    conversation_summary: str | None = None
    relevant_memories: list[str] = field(default_factory=list)
    # M4: current device states from state cache
    home_context_text: str = ""


def assemble_context(
    user_id: str,
    household_id: str,
    current_text: str,
) -> AgentContext:
    """
    Build the full context object for a single agent run.

    Loads profile summaries, recent messages, an optional conversation
    summary, relevant episodic memories, and the current device state snapshot.
    """
    user_profile = get_user_profile(user_id)
    household_profile = get_household_profile(household_id)
    recent_messages = load_recent_messages(user_id)
    conversation_summary = get_conversation_summary(user_id)
    relevant_memories = search_memories(household_id, current_text, user_id)
    home_context_text = _load_home_context(household_id)

    return AgentContext(
        recent_messages=recent_messages,
        user_profile_text=format_profile(user_profile, "User Profile"),
        household_profile_text=format_profile(household_profile, "Household Profile"),
        conversation_summary=conversation_summary,
        relevant_memories=relevant_memories,
        home_context_text=home_context_text,
    )


def _load_home_context(household_id: str) -> str:
    """Return a formatted device-state block, or empty string if none."""
    try:
        from app.homey.state_cache import format_snapshots_for_prompt, get_household_snapshots

        snapshots = get_household_snapshots(household_id)
        return format_snapshots_for_prompt(snapshots)
    except Exception:
        logger.debug("Could not load home context", exc_info=True)
        return ""
