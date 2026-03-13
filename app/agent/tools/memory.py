from __future__ import annotations

import logging

from pydantic_ai import Agent, RunContext

from app.agent.agent import AgentDeps

logger = logging.getLogger(__name__)


def register_memory_tools(agent: Agent[AgentDeps, str]) -> None:
    @agent.tool
    async def store_memory(
        ctx: RunContext[AgentDeps],
        content: str,
        scope: str = "household",
        importance: str = "normal",
    ) -> str:
        """Store a stable fact in long-term memory so it can be recalled in future conversations.

        Call this ONLY when the user explicitly asks you to remember something, or when
        you learn a stable fact that should persist across conversations.

        DO NOT store:
          - Current time, date, or day of the week (always read from your system context)
          - Device states or availability (fetched live from Homey)
          - Error states or service unavailability (temporary, not facts)
          - Anything that will be wrong or misleading tomorrow

        Good examples:
          - "The smart plug in the hallway closet shows total house power consumption."
          - "Kristian prefers concise answers and checks in during his morning commute."
          - "The guest bedroom thermostat is set to 18°C by default."

        Args:
            content: The fact to remember, written as a complete, standalone sentence.
            scope: "household" to share with all household members (default),
                   or "personal" for facts private to this user only.
            importance: How long this memory should be retained when not actively used.
                - "critical"  — never expires (safety, medical, permanent household facts)
                - "important" — retained for ~1 year (strong preferences, recurring patterns)
                - "normal"    — retained for ~3 months (general observations, situational facts)
                - "ephemeral" — retained for ~1 month (short-lived context, one-off details)
        """
        from app.memory.episodic import store_memory as _store_memory

        user_id = ctx.deps.user_id if scope == "personal" else None
        household_id = ctx.deps.household_id

        try:
            _store_memory(
                household_id=household_id,
                content=content,
                user_id=user_id,
                importance=importance,
            )
            scope_label = "personal memory" if scope == "personal" else "household memory"
            logger.info("Stored %s (%s): %.80s", scope_label, importance, content)
            return f"Stored as {scope_label} (importance: {importance})."
        except Exception:
            logger.exception("Failed to store memory")
            return "Failed to store memory — please try again."

    @agent.tool
    async def update_user_profile(
        ctx: RunContext[AgentDeps],
        key: str,
        value: str,
    ) -> str:
        """Update a structured fact in the user's persistent profile.

        Use this to store long-lived personal facts that should always be available
        in future conversations — preferences, routines, identifiers.

        The profile is always included in your context, unlike episodic memories
        which are retrieved by relevance. Use profiles for facts you always need,
        memories for facts that are situationally relevant.

        Good examples:
          key="preferred_language", value="Norwegian"
          key="wake_time", value="07:00"
          key="communication_style", value="concise, technical"
          key="name", value="Kristian"

        Args:
            key: Short snake_case identifier (e.g. "preferred_language").
            value: The value to store.
        """
        from app.memory.profiles import upsert_user_profile

        upsert_user_profile(ctx.deps.user_id, {key: value})
        logger.info("Updated user profile: %s = %.60r", key, value)
        return f"User profile updated: {key} = {value!r}"

    @agent.tool
    async def update_household_profile(
        ctx: RunContext[AgentDeps],
        key: str,
        value: str,
    ) -> str:
        """Update a structured fact in the household's persistent profile.

        Same as update_user_profile but shared across all household members.
        Use for household-wide facts: location, devices, layout, defaults.

        Good examples:
          key="location", value="Oslo, Norway"
          key="timezone", value="Europe/Oslo"
          key="guest_bedroom_default_temp", value="18°C"
          key="front_door_lock", value="Yale Doorman v3"

        Args:
            key: Short snake_case identifier.
            value: The value to store.
        """
        from app.memory.profiles import upsert_household_profile

        upsert_household_profile(ctx.deps.household_id, {key: value})
        logger.info("Updated household profile: %s = %.60r", key, value)
        return f"Household profile updated: {key} = {value!r}"

    @agent.tool
    async def forget_memory(
        ctx: RunContext[AgentDeps],
        content_substring: str,
    ) -> str:
        """Delete a stored memory that is incorrect or outdated.

        Use this when the user asks you to forget something, or when you notice
        a stored memory is wrong and should be removed.

        Args:
            content_substring: A distinctive phrase from the memory to delete.
                               All memories whose content contains this substring
                               (case-insensitive) will be removed.
        """
        from sqlmodel import select

        from app.db import memory_session
        from app.models.memory import EpisodicMemory

        household_id = ctx.deps.household_id
        user_id = ctx.deps.user_id

        with memory_session() as session:
            memories = session.exec(
                select(EpisodicMemory).where(
                    EpisodicMemory.household_id == household_id,
                )
            ).all()

            to_delete = [
                m for m in memories
                if content_substring.lower() in m.content.lower()
                and (m.user_id is None or m.user_id == user_id)
            ]

            if not to_delete:
                return f"No memories found containing '{content_substring}'."

            from app.memory.episodic import _delete_from_vec

            for m in to_delete:
                _delete_from_vec(m.embedding_id)
                session.delete(m)
            session.commit()

        count = len(to_delete)
        logger.info("Deleted %d memory/memories matching %r", count, content_substring)
        return f"Deleted {count} memory entr{'ies' if count != 1 else 'y'}."
