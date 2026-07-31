"""Tests for per-task-type reasoning/thinking level configuration.

See app/agent/llm_router.py parse_thinking_level() / LLMRouter.get_thinking()
/ get_model_settings() — lets .env configure ModelSettings.thinking per
task type (conversation, memory extraction, summarization, world model
extraction) instead of only picking which model to use.
"""

from __future__ import annotations

import pytest

from app.agent.llm_router import LLMRouter, TaskType, parse_thinking_level
from app.config import Settings


def _settings(**overrides: object) -> Settings:
    defaults: dict[str, object] = {
        "telegram_bot_token": "",
        "telegram_webhook_secret": "",
        "app_env": "test",
    }
    defaults.update(overrides)
    return Settings(**defaults)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# parse_thinking_level
# ---------------------------------------------------------------------------


def test_parse_empty_is_none() -> None:
    assert parse_thinking_level("") is None
    assert parse_thinking_level("   ") is None


@pytest.mark.parametrize("raw", ["true", "TRUE", "True", "1", "yes", "on"])
def test_parse_truthy_values(raw: str) -> None:
    assert parse_thinking_level(raw) is True


@pytest.mark.parametrize("raw", ["false", "FALSE", "0", "no", "off"])
def test_parse_falsy_values(raw: str) -> None:
    assert parse_thinking_level(raw) is False


@pytest.mark.parametrize("raw", ["minimal", "low", "medium", "high", "xhigh", "HIGH", " Low "])
def test_parse_effort_levels(raw: str) -> None:
    assert parse_thinking_level(raw) == raw.strip().lower()


def test_parse_unrecognized_value_is_none() -> None:
    # Fail closed — don't silently pass through a typo'd setting.
    assert parse_thinking_level("extreme") is None
    assert parse_thinking_level("maximum") is None


# ---------------------------------------------------------------------------
# LLMRouter.get_thinking
# ---------------------------------------------------------------------------


def test_get_thinking_reads_the_right_field_per_task_type() -> None:
    s = _settings(
        thinking_conversation="high",
        thinking_memory_extraction="low",
        thinking_summarization="minimal",
        thinking_world_model_extraction="xhigh",
    )
    router = LLMRouter(s)
    assert router.get_thinking(TaskType.CONVERSATION) == "high"
    assert router.get_thinking(TaskType.MEMORY_EXTRACTION) == "low"
    assert router.get_thinking(TaskType.SUMMARIZATION) == "minimal"
    assert router.get_thinking(TaskType.WORLD_MODEL_EXTRACTION) == "xhigh"


def test_get_thinking_unset_returns_none() -> None:
    s = _settings()
    router = LLMRouter(s)
    assert router.get_thinking(TaskType.CONVERSATION) is None


def test_get_thinking_unmapped_task_type_returns_none() -> None:
    # HOME_CONTROL/PLANNING/EMBEDDING have no call site and no settings field.
    s = _settings(thinking_conversation="high")
    router = LLMRouter(s)
    assert router.get_thinking(TaskType.HOME_CONTROL) is None
    assert router.get_thinking(TaskType.PLANNING) is None
    assert router.get_thinking(TaskType.EMBEDDING) is None


def test_get_thinking_accepts_bool_setting() -> None:
    s = _settings(thinking_conversation="true")
    router = LLMRouter(s)
    assert router.get_thinking(TaskType.CONVERSATION) is True


# ---------------------------------------------------------------------------
# LLMRouter.get_model_settings
# ---------------------------------------------------------------------------


def test_get_model_settings_none_when_unconfigured() -> None:
    s = _settings()
    router = LLMRouter(s)
    assert router.get_model_settings(TaskType.MEMORY_EXTRACTION) is None


def test_get_model_settings_returns_thinking_dict_when_configured() -> None:
    s = _settings(thinking_summarization="low")
    router = LLMRouter(s)
    result = router.get_model_settings(TaskType.SUMMARIZATION)
    assert result == {"thinking": "low"}
