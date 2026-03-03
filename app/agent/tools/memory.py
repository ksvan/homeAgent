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
        """Store a fact in long-term memory so it can be recalled in future conversations.

        Call this whenever the user explicitly asks you to remember something, or when
        you learn a meaningful fact that should persist across conversations.

        Write content as a clear, self-contained statement — future conversations will
        only see the stored text, not the surrounding conversation.

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
