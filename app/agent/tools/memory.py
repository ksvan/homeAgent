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
        """
        from app.memory.episodic import store_memory as _store_memory

        user_id = ctx.deps.user_id if scope == "personal" else None
        household_id = ctx.deps.household_id

        try:
            _store_memory(
                household_id=household_id,
                content=content,
                user_id=user_id,
            )
            scope_label = "personal memory" if scope == "personal" else "household memory"
            logger.info("Stored %s: %.80s", scope_label, content)
            return f"Stored as {scope_label}."
        except Exception:
            logger.exception("Failed to store memory")
            return "Failed to store memory — please try again."

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

            for m in to_delete:
                session.delete(m)
            session.commit()

        count = len(to_delete)
        logger.info("Deleted %d memory/memories matching %r", count, content_substring)
        return f"Deleted {count} memory entr{'ies' if count != 1 else 'y'}."
