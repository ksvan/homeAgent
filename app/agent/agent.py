from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from pydantic_ai import Agent, AgentRunResult, RunContext
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
    # M3: memory context
    user_profile_text: str = ""
    household_profile_text: str = ""
    conversation_summary: str | None = None
    relevant_memories: list[str] = field(default_factory=list)
    # M4: home context
    home_context_text: str = ""
    # M5: for policy gate — available inside process_tool_call callback via ctx.deps
    user_id: str = ""
    household_id: str = ""
    channel_user_id: str = ""
    # control plane — run identifier threaded through tool callbacks
    run_id: str = ""


def _make_conversation_agent() -> Agent[AgentDeps, str]:
    settings = get_settings()
    model = LLMRouter(settings).get_model(TaskType.CONVERSATION)

    # Attach MCP toolsets for any connected services
    from app.homey.mcp_client import get_mcp_server as get_homey_mcp
    from app.prometheus.mcp_client import get_mcp_server as get_prom_mcp

    toolsets = [s for s in (get_homey_mcp(), get_prom_mcp()) if s is not None]

    a: Agent[AgentDeps, str] = Agent(
        model=model,
        deps_type=AgentDeps,
        output_type=str,
        toolsets=toolsets or None,
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
        base = "\n\n---\n\n".join(parts) if parts else "You are a helpful household assistant."

        extra_sections: list[str] = []
        if d.home_context_text:
            extra_sections.append(d.home_context_text)
        if d.user_profile_text:
            extra_sections.append(d.user_profile_text)
        if d.household_profile_text:
            extra_sections.append(d.household_profile_text)
        if d.conversation_summary:
            extra_sections.append(f"## Conversation Summary\n{d.conversation_summary}")
        if d.relevant_memories:
            mem_block = "\n".join(f"- {m}" for m in d.relevant_memories)
            extra_sections.append(f"## Relevant Memories\n{mem_block}")

        if extra_sections:
            return base + "\n\n---\n\n" + "\n\n".join(extra_sections)
        return base

    from app.agent.tools.actions import register_action_tools
    from app.agent.tools.memory import register_memory_tools
    from app.agent.tools.reminders import register_reminder_tools

    register_reminder_tools(a)
    register_action_tools(a)
    register_memory_tools(a)

    if settings.feature_bash:
        from app.agent.tools.bash import register_bash_tools

        register_bash_tools(a)

    if settings.feature_python:
        from app.agent.tools.python_exec import register_python_tools

        register_python_tools(a)

    if settings.feature_scrape:
        from app.agent.tools.scrape import register_scrape_tools

        register_scrape_tools(a)

    if settings.feature_search:
        from app.agent.tools.search import register_search_tools

        register_search_tools(a)

    return a


_conversation_agent: Agent[AgentDeps, str] | None = None


def get_conversation_agent() -> Agent[AgentDeps, str]:
    global _conversation_agent
    if _conversation_agent is None:
        _conversation_agent = _make_conversation_agent()
    return _conversation_agent


def reload_agent() -> None:
    """Recreate the agent singleton (called on admin /reload or after MCP starts)."""
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
    user_profile_text: str = "",
    household_profile_text: str = "",
    conversation_summary: str | None = None,
    relevant_memories: list[str] | None = None,
    home_context_text: str = "",
    user_id: str = "",
    household_id: str = "",
    channel_user_id: str = "",
    run_id: str = "",
) -> AgentRunResult[str]:
    """
    Run the conversation agent and return the full AgentRunResult.

    Callers should use result.output for the response text, and
    result.new_messages() to inspect tool calls made during the run.
    """
    settings = get_settings()
    try:
        from zoneinfo import ZoneInfo

        tz = ZoneInfo(settings.household_timezone)
    except Exception:
        tz = timezone.utc
    now = datetime.now(tz)

    deps = AgentDeps(
        user_name=user_name,
        agent_name=settings.agent_name,
        household_name=household_name,
        current_date=now.strftime("%A, %d %B %Y"),
        current_time=now.strftime("%H:%M"),
        timezone=settings.household_timezone,
        user_profile_text=user_profile_text,
        household_profile_text=household_profile_text,
        conversation_summary=conversation_summary,
        relevant_memories=relevant_memories or [],
        home_context_text=home_context_text,
        user_id=user_id,
        household_id=household_id,
        channel_user_id=channel_user_id,
        run_id=run_id,
    )

    agent = get_conversation_agent()
    return await agent.run(
        text,
        deps=deps,
        message_history=message_history or [],
    )
