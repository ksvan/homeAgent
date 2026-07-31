"""Tests for app/agent/agent.py — static instructions vs. dynamic system prompt.

Confirms the restructured `_make_conversation_agent()` puts the static
persona/instructions body into `Agent(instructions=...)` (stable across
calls, not re-persisted into message history) and keeps only per-call
content in the `@a.system_prompt` closure. See docs/prompt-caching-design.md.
"""
from __future__ import annotations

from unittest.mock import patch

from pydantic_ai.messages import ModelMessage, ModelResponse, SystemPromptPart, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel

from app.agent.agent import AgentDeps, _make_conversation_agent


def _deps(**overrides: object) -> AgentDeps:
    defaults: dict[str, object] = {
        "user_name": "Alice",
        "agent_name": "Visvas",
        "household_name": "Casa",
        "current_date": "Monday, 1 January 2026",
        "current_time": "08:00 (UTC+01:00)",
        "current_dt_iso": "2026-01-01T08:00:00+01:00",
        "timezone": "Europe/Oslo",
    }
    defaults.update(overrides)
    return AgentDeps(**defaults)  # type: ignore[arg-type]


def _build_agent() -> object:
    with (
        patch("app.agent.agent.LLMRouter.get_model", return_value=TestModel()),
        patch("app.homey.mcp_client.get_mcp_toolset", return_value=None),
        patch("app.prometheus.mcp_client.get_mcp_server", return_value=None),
        patch("app.tools.mcp_client.get_mcp_server", return_value=None),
    ):
        return _make_conversation_agent()


async def _capture_run(agent: object, deps: AgentDeps) -> tuple[str | None, list[ModelMessage]]:
    captured: dict[str, object] = {}

    def _fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        captured["instructions"] = info.instructions
        captured["messages"] = messages
        return ModelResponse(parts=[TextPart("ok")])

    await agent.run("hello", deps=deps, model=FunctionModel(_fn))  # type: ignore[attr-defined]
    return captured["instructions"], captured["messages"]  # type: ignore[return-value]


def _system_prompt_text(messages: list[ModelMessage]) -> str:
    for msg in messages:
        for part in msg.parts:  # type: ignore[attr-defined]
            if isinstance(part, SystemPromptPart):
                return part.content
    raise AssertionError("no SystemPromptPart found in captured messages")


async def test_instructions_are_static_across_different_deps() -> None:
    agent = _build_agent()
    instructions_1, _ = await _capture_run(agent, _deps(user_name="Alice", household_name="Casa"))
    instructions_2, _ = await _capture_run(
        agent, _deps(user_name="Bob", household_name="Other House", current_time="20:00")
    )
    assert instructions_1 == instructions_2
    assert instructions_1  # non-empty


async def test_instructions_contain_no_identity_placeholders() -> None:
    agent = _build_agent()
    instructions, _ = await _capture_run(agent, _deps())
    assert "{agent_name}" not in (instructions or "")
    assert "{user_name}" not in (instructions or "")


async def test_dynamic_system_prompt_differs_with_deps() -> None:
    agent = _build_agent()
    _, messages_1 = await _capture_run(agent, _deps(user_name="Alice", household_name="Casa"))
    _, messages_2 = await _capture_run(
        agent, _deps(user_name="Bob", household_name="Other House")
    )
    assert _system_prompt_text(messages_1) != _system_prompt_text(messages_2)


async def test_identity_names_appear_in_dynamic_part_not_static_part() -> None:
    agent = _build_agent()
    instructions, messages = await _capture_run(
        agent, _deps(user_name="Zephyrine", household_name="Wintermute House")
    )
    system_text = _system_prompt_text(messages)
    assert "Zephyrine" in system_text
    assert "Wintermute House" in system_text
    assert "Zephyrine" not in (instructions or "")
    assert "Wintermute House" not in (instructions or "")


async def test_dynamic_system_prompt_includes_time_context() -> None:
    agent = _build_agent()
    _, messages = await _capture_run(agent, _deps())
    system_text = _system_prompt_text(messages)
    assert "<time_context>" in system_text
    assert "2026-01-01T08:00:00+01:00" in system_text
