"""Unit tests for app.agent.llm_router — provider detection and model chain."""
from __future__ import annotations

import pytest
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from app.agent.llm_router import LLMRouter, TaskType, provider_for_model
from app.config import Settings


def _settings(**overrides: object) -> Settings:
    """Settings with all LLM key/model fields pinned so tests never depend
    on the real .env file."""
    defaults: dict[str, object] = {
        "telegram_bot_token": "",
        "telegram_webhook_secret": "",
        "app_env": "test",
        "anthropic_api_key": "sk-ant-global",
        "openai_api_key": "sk-openai-global",
        "model_primary_api_key": "",
        "model_background_api_key": "",
        "model_fallback_api_key": "",
        "model_primary": "claude-sonnet-5",
        "model_background": "claude-haiku-4-5-20251001",
        "model_fallback": "gpt-4o",
        "feature_cheap_background_models": True,
        "feature_fallback_model": True,
    }
    defaults.update(overrides)
    return Settings(**defaults)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# provider_for_model — single source of truth for provider detection
# ---------------------------------------------------------------------------


def test_provider_for_model_claude() -> None:
    assert provider_for_model("claude-sonnet-5") == "anthropic"
    assert provider_for_model("claude-haiku-4-5-20251001") == "anthropic"


def test_provider_for_model_openai() -> None:
    assert provider_for_model("gpt-5.6") == "openai"
    assert provider_for_model("gpt-4o") == "openai"
    assert provider_for_model("o3-mini") == "openai"


# ---------------------------------------------------------------------------
# get_model — instantiates the correct provider class regardless of key shape
# ---------------------------------------------------------------------------


def test_get_model_returns_anthropic_for_claude_model() -> None:
    s = _settings(model_primary="claude-sonnet-5")
    model = LLMRouter(s).get_model(TaskType.CONVERSATION)
    assert isinstance(model, AnthropicModel)


def test_get_model_returns_openai_for_gpt_model() -> None:
    s = _settings(model_primary="gpt-5.6")
    model = LLMRouter(s).get_model(TaskType.CONVERSATION)
    assert isinstance(model, OpenAIChatModel)


def test_get_model_uses_model_name_not_key_prefix() -> None:
    # A claude-* model name bound to a per-slot key that happens to have an
    # OpenAI-shaped prefix must still resolve to AnthropicModel — provider
    # selection is driven by the model name, never by sniffing the key.
    s = _settings(model_primary="claude-sonnet-5", model_primary_api_key="sk-svcacct-oops")
    model = LLMRouter(s).get_model(TaskType.CONVERSATION)
    assert isinstance(model, AnthropicModel)


# ---------------------------------------------------------------------------
# get_model_chain — ordering and skipping unconfigured slots
# ---------------------------------------------------------------------------


def test_chain_conversation_is_primary_then_fallback() -> None:
    s = _settings(model_primary="gpt-5.6", model_fallback="claude-sonnet-5")
    chain = LLMRouter(s).get_model_chain(TaskType.CONVERSATION)
    assert len(chain) == 2
    assert isinstance(chain[0], OpenAIChatModel)
    assert isinstance(chain[1], AnthropicModel)


def test_chain_skips_fallback_when_feature_disabled() -> None:
    s = _settings(feature_fallback_model=False)
    chain = LLMRouter(s).get_model_chain(TaskType.CONVERSATION)
    assert len(chain) == 1


def test_chain_skips_fallback_when_no_key_resolves() -> None:
    s = _settings(
        model_primary="gpt-5.6",
        model_fallback="claude-sonnet-5",
        anthropic_api_key="",
        model_fallback_api_key="",
    )
    chain = LLMRouter(s).get_model_chain(TaskType.CONVERSATION)
    assert len(chain) == 1


def test_chain_background_task_prefers_cheap_model() -> None:
    s = _settings()
    chain = LLMRouter(s).get_model_chain(TaskType.MEMORY_EXTRACTION)
    assert isinstance(chain[0], AnthropicModel)  # model_background is a claude model
    assert len(chain) == 3  # background, primary, fallback


def test_chain_background_task_falls_through_when_cheap_disabled() -> None:
    s = _settings(feature_cheap_background_models=False, model_primary="gpt-5.6")
    chain = LLMRouter(s).get_model_chain(TaskType.MEMORY_EXTRACTION)
    assert isinstance(chain[0], OpenAIChatModel)  # falls straight to model_primary


def test_get_model_raises_when_nothing_configured() -> None:
    s = _settings(anthropic_api_key="", openai_api_key="", feature_fallback_model=False)
    with pytest.raises(RuntimeError, match="No LLM provider configured"):
        LLMRouter(s).get_model(TaskType.CONVERSATION)


# ---------------------------------------------------------------------------
# get_thinking / get_model_settings — gated by whether the given model
# actually supports the reasoning/thinking parameter. A run can fail over
# from Claude to a classic OpenAI model (gpt-4o) that rejects
# reasoning_effort outright, especially with tools attached.
# ---------------------------------------------------------------------------


def test_thinking_is_applied_for_claude_model() -> None:
    s = _settings(thinking_conversation="high")
    assert LLMRouter(s).get_thinking(TaskType.CONVERSATION, "claude-sonnet-5") == "high"


def test_thinking_is_omitted_for_classic_openai_fallback_model() -> None:
    s = _settings(thinking_conversation="high")
    assert LLMRouter(s).get_thinking(TaskType.CONVERSATION, "gpt-4o") is None
    assert LLMRouter(s).get_thinking(TaskType.CONVERSATION, "gpt-4o-mini") is None


def test_thinking_is_applied_for_openai_reasoning_model() -> None:
    s = _settings(thinking_conversation="medium")
    assert LLMRouter(s).get_thinking(TaskType.CONVERSATION, "gpt-5.6") == "medium"
    assert LLMRouter(s).get_thinking(TaskType.CONVERSATION, "o3-mini") == "medium"


def test_get_model_settings_omits_thinking_for_unsupported_model() -> None:
    s = _settings(thinking_memory_extraction="high")
    model = LLMRouter(s).get_model(TaskType.MEMORY_EXTRACTION)  # background model = claude
    settings = LLMRouter(s).get_model_settings(TaskType.MEMORY_EXTRACTION, model)
    assert settings == {"thinking": "high"}

    fallback_model = OpenAIChatModel("gpt-4o-mini", provider=OpenAIProvider(api_key="sk-x"))
    settings = LLMRouter(s).get_model_settings(TaskType.MEMORY_EXTRACTION, fallback_model)
    assert settings is None
