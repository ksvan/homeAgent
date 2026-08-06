"""Tests for app.agent.agent._build_model_settings — prompt caching config.

See docs/prompt-caching-design.md for the rationale: Anthropic gets real
explicit cache_control breakpoints; OpenAI/GPT-5.6 gets a deliberate
mode="explicit" with no breakpoints placed, which disables implicit-mode
caching (pydantic-ai has no system-message-level breakpoint hook for it).
"""
from __future__ import annotations

from app.agent.agent import _build_model_settings
from app.config import Settings


def _settings(**overrides: object) -> Settings:
    defaults: dict[str, object] = {
        "telegram_bot_token": "",
        "telegram_webhook_secret": "",
        "app_env": "test",
        "max_tokens_per_run": 4096,
    }
    defaults.update(overrides)
    return Settings(**defaults)  # type: ignore[arg-type]


def test_caching_enabled_sets_anthropic_breakpoints() -> None:
    settings = _settings(feature_prompt_caching=True)
    result = _build_model_settings(settings)
    assert result["anthropic_cache_instructions"] == "5m"  # type: ignore[typeddict-item]
    assert result["anthropic_cache_tool_definitions"] == "5m"  # type: ignore[typeddict-item]


def test_caching_enabled_disables_openai_implicit_mode() -> None:
    settings = _settings(feature_prompt_caching=True)
    result = _build_model_settings(settings)
    assert result["openai_prompt_cache_options"] == {"mode": "explicit"}  # type: ignore[typeddict-item]


def test_caching_enabled_omits_openai_cache_key_and_retention() -> None:
    # Both are meaningless once caching is explicitly turned off — see the
    # design doc's decision-gate resolution.
    settings = _settings(feature_prompt_caching=True)
    result = _build_model_settings(settings)
    assert "openai_prompt_cache_key" not in result
    assert "openai_prompt_cache_retention" not in result


def test_caching_disabled_omits_all_caching_keys() -> None:
    settings = _settings(feature_prompt_caching=False)
    result = _build_model_settings(settings)
    assert "anthropic_cache_instructions" not in result
    assert "anthropic_cache_tool_definitions" not in result
    assert "openai_prompt_cache_options" not in result


def test_max_tokens_always_present() -> None:
    for flag in (True, False):
        settings = _settings(feature_prompt_caching=flag, max_tokens_per_run=1234)
        result = _build_model_settings(settings)
        assert result["max_tokens"] == 1234  # type: ignore[typeddict-item]
