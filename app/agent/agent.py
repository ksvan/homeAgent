from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, cast

from pydantic_ai import Agent, AgentRunResult, RunContext
from pydantic_ai.messages import ModelMessage
from pydantic_ai.models import Model
from pydantic_ai.toolsets import AbstractToolset

if TYPE_CHECKING:
    from pydantic_ai.settings import ModelSettings

    from app.channels.base import MediaAttachment
    from app.config import Settings

from app.agent.llm_router import LLMRouter, TaskType
from app.agent.prompts import load_static_prompt_body, render_identity_block
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
    current_user_text: str = ""
    active_task_text: str = ""
    conversation_summary: str | None = None
    relevant_memories: list[str] = field(default_factory=list)
    # M5: for policy gate — available inside process_tool_call callback via ctx.deps
    user_id: str = ""
    household_id: str = ""
    channel_user_id: str = ""
    # which Channel adapter this run's channel_user_id belongs to (e.g.
    # "telegram", "web") — used by the policy gate / verify-after-write to
    # route mid-run confirmation prompts and follow-ups to the right place.
    channel: str = "telegram"
    # control plane — run identifier threaded through tool callbacks
    run_id: str = ""
    # Phase 3b: control task for the current event-driven run (if any)
    control_task_id: str = ""


def _make_conversation_agent() -> Agent[AgentDeps, str]:
    settings = get_settings()
    model = LLMRouter(settings).get_model(TaskType.CONVERSATION)

    # Attach MCP toolsets for any connected services
    from app.homey.mcp_client import get_mcp_toolset
    from app.oda.mcp_client import get_mcp_server as get_oda_mcp
    from app.prometheus.mcp_client import get_mcp_server as get_prom_mcp
    from app.tools.mcp_client import get_mcp_server as get_tools_mcp

    homey_ts = get_mcp_toolset(advanced=False)
    prom_ts = get_prom_mcp()
    tools_ts = get_tools_mcp()
    # Two-gate: feature_oda AND an actually-connected household account —
    # get_oda_mcp() is already None if the household never connected, but
    # the flag lets an operator disable the tool without disconnecting.
    oda_ts = get_oda_mcp() if settings.feature_oda else None
    toolsets: list[AbstractToolset[AgentDeps]] = [
        cast(AbstractToolset[AgentDeps], s)
        for s in (homey_ts, prom_ts, tools_ts, oda_ts)
        if s is not None
    ]
    logger.info(
        "Building agent: homey=%s prom=%s tools=%s oda=%s total_toolsets=%d",
        "ok" if homey_ts is not None else "MISSING",
        "ok" if prom_ts is not None else "missing",
        "ok" if tools_ts is not None else "missing",
        "ok" if oda_ts is not None else "missing",
        len(toolsets),
    )

    a: Agent[AgentDeps, str] = Agent(
        model=model,
        deps_type=AgentDeps,
        output_type=str,
        toolsets=toolsets or None,
        retries=3,
        instructions=load_static_prompt_body() or "You are a helpful household assistant.",
    )

    @a.system_prompt
    async def _(ctx: RunContext[AgentDeps]) -> str:  # noqa: ANN202
        import json as _json

        d = ctx.deps

        # Dynamic suffix only — the static persona/instructions body lives in
        # Agent(instructions=...) above so it forms a stable, cacheable prefix
        # (see docs/prompt-caching-design.md). Ordered most-stable →
        # most-volatile: identity, time, profiles, world model, skills index,
        # active task, conversation summary, relevant memories.
        from app.agent.skills import get_skill_registry

        identity = render_identity_block(d.agent_name, d.household_name, d.user_name)
        time_block = (
            "<time_context>\n"
            + _json.dumps({"current_time": d.current_dt_iso, "timezone": d.timezone}, indent=2)
            + "\n</time_context>"
        )

        sections: list[str] = [s for s in (identity, time_block) if s]
        if d.user_profile_text:
            sections.append(d.user_profile_text)
        if d.household_profile_text:
            sections.append(d.household_profile_text)
        if d.current_user_text:
            sections.append(d.current_user_text)
        if d.world_model_text:
            sections.append(d.world_model_text)
        skills_index = get_skill_registry().skills_index_text()
        if skills_index:
            sections.append(skills_index)
        if d.active_task_text:
            sections.append(d.active_task_text)
        if d.conversation_summary:
            sections.append(f"## Conversation Summary\n{d.conversation_summary}")
        if d.relevant_memories:
            mem_block = "\n".join(f"- {m}" for m in d.relevant_memories)
            sections.append(f"## Relevant Memories\n{mem_block}")

        return "\n\n---\n\n".join(sections)

    from app.agent.tools.actions import register_action_tools
    from app.agent.tools.calendar import register_calendar_tools
    from app.agent.tools.event_rules import register_event_rule_tools
    from app.agent.tools.memory import register_memory_tools
    from app.agent.tools.reminders import register_reminder_tools
    from app.agent.tools.scheduled_prompts import register_scheduled_prompt_tools
    from app.agent.tools.skills import register_skills_tools
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
    register_skills_tools(a)

    if settings.feature_wine:
        from app.agent.tools.wine import register_wine_tools

        register_wine_tools(a)
        logger.info("Wine cellar tools registered")

    if settings.feature_flight_monitor:
        from app.agent.tools.flights import register_flight_tools

        register_flight_tools(a)
        logger.info("Flight monitor tools registered")

    if settings.feature_email_channel:
        from app.agent.tools.email import register_email_tools

        register_email_tools(a)
        logger.info("Email channel tools registered")

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
    from app.agent.skills import reload_skill_registry

    clear_prompt_cache()
    reload_skill_registry()
    logger.info("Agent and prompt cache cleared — will reinitialise on next request")


async def run_conversation(
    text: str,
    user_name: str,
    household_name: str = "the household",
    message_history: list[ModelMessage] | None = None,
    user_profile_text: str = "",
    household_profile_text: str = "",
    world_model_text: str = "",
    current_user_text: str = "",
    active_task_text: str = "",
    conversation_summary: str | None = None,
    relevant_memories: list[str] | None = None,
    user_id: str = "",
    household_id: str = "",
    channel_user_id: str = "",
    channel: str = "telegram",
    run_id: str = "",
    control_task_id: str = "",
    media: "list[MediaAttachment] | None" = None,
    model: Model | None = None,
) -> AgentRunResult[str]:
    """
    Run the conversation agent and return the full AgentRunResult.

    Callers should use result.output for the response text, and
    result.new_messages() to inspect tool calls made during the run.

    Args:
        model: Optional model override for this run only (e.g. a fallback
               provider after the default model's API call failed). Leaves
               the agent's tools/toolsets/system prompt untouched.
    """
    import datetime as _dt

    settings = get_settings()
    try:
        from zoneinfo import ZoneInfo

        tz: _dt.tzinfo = ZoneInfo(settings.household_timezone)
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
        current_user_text=current_user_text,
        active_task_text=active_task_text,
        conversation_summary=conversation_summary,
        relevant_memories=relevant_memories or [],
        user_id=user_id,
        household_id=household_id,
        channel_user_id=channel_user_id,
        channel=channel,
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

    if media:
        media_parts: list[str | BinaryContent] = [text] + [
            BinaryContent(data=m.data, media_type=m.mime_type) for m in media
        ]
        user_prompt: str | list[str | BinaryContent] = media_parts
        logger.info("run_conversation: media=%d attachment(s)", len(media))
    else:
        user_prompt = text

    return await agent.run(
        user_prompt,
        deps=deps,
        message_history=message_history or [],
        model_settings=_build_model_settings(settings),
        model=model,
    )


def _build_model_settings(settings: "Settings") -> "ModelSettings":
    """Build the model_settings dict passed to agent.run().

    Cross-provider by construction: extra provider-specific keys are ignored
    by whichever provider isn't handling the call (primary or fallback — see
    app/agent/llm_router.py get_model_chain()). See
    docs/prompt-caching-design.md for the caching rationale.
    """
    raw: dict[str, object] = {"max_tokens": settings.max_tokens_per_run}
    if settings.feature_prompt_caching:
        raw["anthropic_cache_instructions"] = "5m"
        raw["anthropic_cache_tool_definitions"] = "5m"
        # OpenAI/GPT-5.6: deliberately off, not "unset" — implicit mode is the
        # server-side default when this field is absent, and it would pay the
        # cache-write premium against our necessarily-dynamic prefix with no
        # offsetting reads. See docs/prompt-caching-design.md.
        raw["openai_prompt_cache_options"] = {"mode": "explicit"}

    thinking = LLMRouter(settings).get_thinking(TaskType.CONVERSATION)
    if thinking is not None:
        raw["thinking"] = thinking

    return cast("ModelSettings", raw)
