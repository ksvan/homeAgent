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
    world_model_text: str = ""
    current_user_text: str = ""
    active_task_text: str = ""
    conversation_summary: str | None = None
    relevant_memories: list[str] = field(default_factory=list)


def assemble_context(
    user_id: str,
    household_id: str,
    current_text: str,
) -> AgentContext:
    """
    Build the full context object for a single agent run.

    Loads profile summaries, world model snapshot, recent messages,
    an optional conversation summary, and relevant episodic memories.
    """
    from app.tasks.service import get_active_task_context
    from app.world.formatter import format_world_model

    user_profile = get_user_profile(user_id)
    household_profile = get_household_profile(household_id)
    world_model_text = format_world_model(household_id, current_user_id=user_id)
    current_user_text = _build_current_user_section(household_id, user_id)
    active_task_text = get_active_task_context(user_id)
    recent_messages = load_recent_messages(user_id)
    conversation_summary = get_conversation_summary(user_id)
    relevant_memories = search_memories(household_id, current_text, user_id)

    return AgentContext(
        recent_messages=recent_messages,
        user_profile_text=format_profile(user_profile, "User Profile"),
        household_profile_text=format_profile(household_profile, "Household Profile"),
        world_model_text=world_model_text,
        current_user_text=current_user_text,
        active_task_text=active_task_text,
        conversation_summary=conversation_summary,
        relevant_memories=relevant_memories,
    )


def _build_current_user_section(household_id: str, user_id: str) -> str:
    """Return a compact Current User block for grounding the model."""
    from sqlmodel import select

    from app.db import users_session
    from app.models.users import User
    from app.world.repository import WorldModelRepository

    with users_session() as session:
        user = session.exec(select(User).where(User.id == user_id)).first()
    if not user:
        return ""

    member = WorldModelRepository.get_member_for_user(household_id, user_id)
    role = "admin" if user.is_admin else "member"
    member_name = member.name if member else user.name
    member_id = member.id if member else "(not linked)"

    lines = [
        "## Current User",
        f"- user_id: {user_id}",
        f"- name: {user.name}",
        f"- household_member_id: {member_id}",
        f"- household_member_name: {member_name}",
        f"- role: {role}",
    ]
    return "\n".join(lines)
