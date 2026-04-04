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
    current_dt_iso: str  # ISO 8601 with UTC offset, e.g. "2026-03-20T15:32:00+01:00"
    timezone: str
    # M3: memory context
    user_profile_text: str = ""
    household_profile_text: str = ""
    world_model_text: str = ""
    active_task_text: str = ""
    conversation_summary: str | None = None
    relevant_memories: list[str] = field(default_factory=list)
    # M5: for policy gate — available inside process_tool_call callback via ctx.deps
    user_id: str = ""
    household_id: str = ""
    channel_user_id: str = ""
    # control plane — run identifier threaded through tool callbacks
    run_id: str = ""
    # Phase 3b: control task for the current event-driven run (if any)
    control_task_id: str = ""


def _make_conversation_agent() -> Agent[AgentDeps, str]:
    settings = get_settings()
    model = LLMRouter(settings).get_model(TaskType.CONVERSATION)

    # Attach MCP toolsets for any connected services
    from app.homey.mcp_client import get_mcp_toolset
    from app.prometheus.mcp_client import get_mcp_server as get_prom_mcp
    from app.tools.mcp_client import get_mcp_server as get_tools_mcp

    homey_ts = get_mcp_toolset(advanced=False)
    prom_ts = get_prom_mcp()
    tools_ts = get_tools_mcp()
    toolsets = [s for s in (homey_ts, prom_ts, tools_ts) if s is not None]
    logger.info(
        "Building agent: homey=%s prom=%s tools=%s total_toolsets=%d",
        "ok" if homey_ts is not None else "MISSING",
        "ok" if prom_ts is not None else "missing",
        "ok" if tools_ts is not None else "missing",
        len(toolsets),
    )

    a: Agent[AgentDeps, str] = Agent(
        model=model,
        deps_type=AgentDeps,
        output_type=str,
        toolsets=toolsets or None,
        retries=3,
    )

    @a.system_prompt
    async def _(ctx: RunContext[AgentDeps]) -> str:  # noqa: ANN202
        import json as _json

        d = ctx.deps
        vars_: dict[str, str] = {
            "agent_name": d.agent_name,
            "household_name": d.household_name,
            "user_name": d.user_name,
            "current_date": d.current_date,
            "current_time": d.current_time,
            "timezone": d.timezone,
        }
        persona = load_persona(vars_)
        instructions = load_instructions(vars_)

        parts = [p for p in (persona, instructions) if p]
        base = "\n\n---\n\n".join(parts) if parts else "You are a helpful household assistant."

        extra_sections: list[str] = []
        if d.user_profile_text:
            extra_sections.append(d.user_profile_text)
        if d.household_profile_text:
            extra_sections.append(d.household_profile_text)
        if d.world_model_text:
            extra_sections.append(d.world_model_text)
        if d.active_task_text:
            extra_sections.append(d.active_task_text)
        if d.conversation_summary:
            extra_sections.append(f"## Conversation Summary\n{d.conversation_summary}")
        if d.relevant_memories:
            mem_block = "\n".join(f"- {m}" for m in d.relevant_memories)
            extra_sections.append(f"## Relevant Memories\n{mem_block}")

        time_block = (
            "<time_context>\n"
            + _json.dumps({"current_time": d.current_dt_iso, "timezone": d.timezone}, indent=2)
            + "\n</time_context>"
        )
        suffix = ("\n\n---\n\n" + "\n\n".join(extra_sections)) if extra_sections else ""
        return time_block + "\n\n" + base + suffix

    from app.agent.tools.actions import register_action_tools
    from app.agent.tools.calendar import register_calendar_tools
    from app.agent.tools.event_rules import register_event_rule_tools
    from app.agent.tools.memory import register_memory_tools
    from app.agent.tools.reminders import register_reminder_tools
    from app.agent.tools.scheduled_prompts import register_scheduled_prompt_tools
    from app.agent.tools.tasks import register_task_tools
    from app.agent.tools.world_model import register_world_model_tools

    register_reminder_tools(a)
    register_action_tools(a)
    register_memory_tools(a)
    register_calendar_tools(a)
    register_scheduled_prompt_tools(a)
    register_world_model_tools(a)
    register_task_tools(a)
    register_event_rule_tools(a)

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
    world_model_text: str = "",
    active_task_text: str = "",
    conversation_summary: str | None = None,
    relevant_memories: list[str] | None = None,
    user_id: str = "",
    household_id: str = "",
    channel_user_id: str = "",
    run_id: str = "",
    control_task_id: str = "",
    media: list | None = None,
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
        current_time=(
            now.strftime("%H:%M")
            + " (UTC"
            + now.strftime("%z")[:3]
            + ":"
            + now.strftime("%z")[3:]
            + ")"
        ),
        current_dt_iso=now.isoformat(),
        timezone=settings.household_timezone,
        user_profile_text=user_profile_text,
        household_profile_text=household_profile_text,
        world_model_text=world_model_text,
        active_task_text=active_task_text,
        conversation_summary=conversation_summary,
        relevant_memories=relevant_memories or [],
        user_id=user_id,
        household_id=household_id,
        channel_user_id=channel_user_id,
        run_id=run_id,
        control_task_id=control_task_id,
    )

    agent = get_conversation_agent()

    from app.homey.mcp_client import get_mcp_server as _get_homey
    _homey = _get_homey()
    logger.info(
        "run_conversation: homey_mcp=%s running_count=%s agent_toolsets=%d",
        "connected" if _homey is not None else "DISCONNECTED",
        getattr(_homey, "_running_count", "N/A"),
        len(list(agent.toolsets)),
    )

    from pydantic_ai import BinaryContent
    from pydantic_ai.settings import ModelSettings

    if media:
        user_prompt: str | list = [text] + [
            BinaryContent(data=m.data, media_type=m.mime_type) for m in media
        ]
        logger.info("run_conversation: media=%d attachment(s)", len(media))
    else:
        user_prompt = text

    return await agent.run(
        user_prompt,
        deps=deps,
        message_history=message_history or [],
        model_settings=ModelSettings(max_tokens=settings.max_tokens_per_run),
    )
