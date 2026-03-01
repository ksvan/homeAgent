from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from pydantic_ai import Agent, RunContext
from pydantic_ai.messages import ModelMessage

from app.agent.llm_router import LLMRouter, TaskType
from app.agent.prompts import load_instructions, load_persona
from app.config import get_settings

logger = logging.getLogger(__name__)


@dataclass
class AgentDeps:
    user_name: str
    agent_name: str
    household_name: str
    current_date: str
    current_time: str
    timezone: str


def _make_conversation_agent() -> Agent[AgentDeps, str]:
    settings = get_settings()
    model = LLMRouter(settings).get_model(TaskType.CONVERSATION)

    a: Agent[AgentDeps, str] = Agent(
        model=model,
        deps_type=AgentDeps,
        output_type=str,
    )

    @a.system_prompt
    async def _(ctx: RunContext[AgentDeps]) -> str:  # noqa: ANN202
        d = ctx.deps
        vars_: dict[str, str] = {
            "agent_name": d.agent_name,
            "household_name": d.household_name,
            "current_date": d.current_date,
            "current_time": d.current_time,
            "timezone": d.timezone,
        }
        persona = load_persona(vars_)
        instructions = load_instructions(vars_)
        parts = [p for p in (persona, instructions) if p]
        return "\n\n---\n\n".join(parts) if parts else "You are a helpful household assistant."

    return a


_conversation_agent: Agent[AgentDeps, str] | None = None


def get_conversation_agent() -> Agent[AgentDeps, str]:
    global _conversation_agent
    if _conversation_agent is None:
        _conversation_agent = _make_conversation_agent()
    return _conversation_agent


def reload_agent() -> None:
    """Recreate the agent singleton (called on admin /reload)."""
    global _conversation_agent
    _conversation_agent = None
    from app.agent.prompts import clear_prompt_cache

    clear_prompt_cache()
    logger.info("Agent and prompt cache cleared — will reinitialise on next request")


async def run_conversation(
    text: str,
    user_name: str,
    household_name: str = "the household",
    message_history: list[ModelMessage] | None = None,
) -> str:
    settings = get_settings()
    now = datetime.now(timezone.utc)

    deps = AgentDeps(
        user_name=user_name,
        agent_name=settings.agent_name,
        household_name=household_name,
        current_date=now.strftime("%A, %d %B %Y"),
        current_time=now.strftime("%H:%M"),
        timezone="UTC",  # M3: use household timezone from DB profile
    )

    agent = get_conversation_agent()
    result = await agent.run(
        text,
        deps=deps,
        message_history=message_history or [],
    )
    return str(result.output)
