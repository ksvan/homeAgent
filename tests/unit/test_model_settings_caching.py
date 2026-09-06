"""Tests for app.agent.agent._build_model_settings — caching + thinking config.

See docs/prompt-caching-design.md for the caching rationale: Anthropic gets
real explicit cache_control breakpoints; OpenAI/GPT-5.6 gets a deliberate
mode="explicit" with no breakpoints placed, which disables implicit-mode
caching (pydantic-ai has no system-message-level breakpoint hook for it).

Also covers the THINKING_CONVERSATION setting (per-task-type reasoning
level — see app/agent/llm_router.py get_thinking()) and its gating by
whichever model the call is actually going to (see
test_thinking_omitted_for_openai_fallback_model — a run that fails over
from Claude to gpt-4o must not carry a reasoning_effort request that model
rejects outright).
"""

from __future__ import annotations

from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.anthropic import AnthropicProvider
from pydantic_ai.providers.openai import OpenAIProvider

from app.agent.agent import _build_model_settings
from app.config import Settings

_CLAUDE = AnthropicModel("claude-sonnet-5", provider=AnthropicProvider(api_key="sk-ant-x"))
_GPT4O = OpenAIChatModel("gpt-4o", provider=OpenAIProvider(api_key="sk-x"))


def _settings(**overrides: object) -> Settings:
    defaults: dict[str, object] = {
        "telegram_bot_token": "",
        "telegram_webhook_secret": "",
        "app_env": "test",
        "max_tokens_per_run": 4096,
        # Explicit "unset" — otherwise pydantic-settings loads whatever the
        # developer's local .env has for these, breaking test isolation.
        "thinking_conversation": "",
        "thinking_memory_extraction": "",
        "thinking_summarization": "",
        "thinking_world_model_extraction": "",
    }
    defaults.update(overrides)
    return Settings(**defaults)  # type: ignore[arg-type]


def test_caching_enabled_sets_anthropic_breakpoints() -> None:
    settings = _settings(feature_prompt_caching=True)
    result = _build_model_settings(settings, _CLAUDE)
    assert result["anthropic_cache_instructions"] == "5m"  # type: ignore[typeddict-item]
    assert result["anthropic_cache_tool_definitions"] == "5m"  # type: ignore[typeddict-item]


def test_caching_enabled_disables_openai_implicit_mode() -> None:
    settings = _settings(feature_prompt_caching=True)
    result = _build_model_settings(settings, _CLAUDE)
    assert result["openai_prompt_cache_options"] == {"mode": "explicit"}  # type: ignore[typeddict-item]


def test_caching_enabled_omits_openai_cache_key_and_retention() -> None:
    # Both are meaningless once caching is explicitly turned off — see the
    # design doc's decision-gate resolution.
    settings = _settings(feature_prompt_caching=True)
    result = _build_model_settings(settings, _CLAUDE)
    assert "openai_prompt_cache_key" not in result
    assert "openai_prompt_cache_retention" not in result


def test_caching_disabled_omits_all_caching_keys() -> None:
    settings = _settings(feature_prompt_caching=False)
    result = _build_model_settings(settings, _CLAUDE)
    assert "anthropic_cache_instructions" not in result
    assert "anthropic_cache_tool_definitions" not in result
    assert "openai_prompt_cache_options" not in result


def test_max_tokens_always_present() -> None:
    for flag in (True, False):
        settings = _settings(feature_prompt_caching=flag, max_tokens_per_run=1234)
        result = _build_model_settings(settings, _CLAUDE)
        assert result["max_tokens"] == 1234  # type: ignore[typeddict-item]


def test_thinking_omitted_when_unset() -> None:
    settings = _settings()
    result = _build_model_settings(settings, _CLAUDE)
    assert "thinking" not in result


def test_thinking_included_when_configured_for_claude() -> None:
    settings = _settings(thinking_conversation="xhigh")
    result = _build_model_settings(settings, _CLAUDE)
    assert result["thinking"] == "xhigh"  # type: ignore[typeddict-item]


def test_thinking_bool_setting_passed_through() -> None:
    settings = _settings(thinking_conversation="false")
    result = _build_model_settings(settings, _CLAUDE)
    assert result["thinking"] is False  # type: ignore[typeddict-item]


def test_thinking_independent_of_caching_flag() -> None:
    settings = _settings(thinking_conversation="high", feature_prompt_caching=False)
    result = _build_model_settings(settings, _CLAUDE)
    assert result["thinking"] == "high"  # type: ignore[typeddict-item]
    assert "anthropic_cache_instructions" not in result


def test_thinking_omitted_for_openai_fallback_model() -> None:
    # Regression: a run that fails over from the Claude primary to the
    # gpt-4o fallback (app/agent/runner.py's model chain) must not carry a
    # reasoning_effort request — gpt-4o rejects it outright, especially
    # with the conversation agent's tools attached.
    settings = _settings(thinking_conversation="high")
    result = _build_model_settings(settings, _GPT4O)
    assert "thinking" not in result
