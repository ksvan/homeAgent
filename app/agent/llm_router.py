from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING, cast

from pydantic_ai.models import Model
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.anthropic import AnthropicProvider
from pydantic_ai.providers.openai import OpenAIProvider

from app.config import Settings, get_settings

if TYPE_CHECKING:
    from pydantic_ai.settings import ModelSettings


class TaskType(str, Enum):
    CONVERSATION = "CONVERSATION"
    HOME_CONTROL = "HOME_CONTROL"
    PLANNING = "PLANNING"
    MEMORY_EXTRACTION = "MEMORY_EXTRACTION"
    SUMMARIZATION = "SUMMARIZATION"
    WORLD_MODEL_EXTRACTION = "WORLD_MODEL_EXTRACTION"
    EMBEDDING = "EMBEDDING"


_BACKGROUND_TASK_TYPES = (
    TaskType.MEMORY_EXTRACTION,
    TaskType.SUMMARIZATION,
    TaskType.WORLD_MODEL_EXTRACTION,
)

_THINKING_LEVELS = frozenset({"minimal", "low", "medium", "high", "xhigh"})

# Task types that actually have an agent.run() call site today (see
# app/agent/agent.py, app/memory/extraction.py, app/memory/conversation.py,
# app/world/extraction.py) — the only ones a per-task thinking setting can
# affect. HOME_CONTROL/PLANNING/EMBEDDING have no corresponding settings.
_THINKING_SETTINGS_FIELD: dict[TaskType, str] = {
    TaskType.CONVERSATION: "thinking_conversation",
    TaskType.MEMORY_EXTRACTION: "thinking_memory_extraction",
    TaskType.SUMMARIZATION: "thinking_summarization",
    TaskType.WORLD_MODEL_EXTRACTION: "thinking_world_model_extraction",
}


def parse_thinking_level(raw: str) -> bool | str | None:
    """Parse a THINKING_* env value into pydantic-ai's ModelSettings.thinking shape.

    Empty/unset -> None (provider default, key omitted entirely).
    true/false (any case) -> bool.
    minimal/low/medium/high/xhigh -> that literal string.
    Anything else -> None (treated as unset; callers should not silently
    apply a value that failed to parse).
    """
    value = raw.strip().lower()
    if not value:
        return None
    if value in ("true", "1", "yes", "on"):
        return True
    if value in ("false", "0", "no", "off"):
        return False
    if value in _THINKING_LEVELS:
        return value
    return None


def provider_for_model(model_name: str) -> str:
    """Return "anthropic" or "openai" for a given model name.

    Single source of truth for provider detection. Both API key resolution
    and model instantiation must use this so a model name and the provider
    it's actually sent to can never disagree.
    """
    return "anthropic" if model_name.startswith("claude") else "openai"


def _model_supports_thinking(model_name: str) -> bool:
    """Whether `model_name` accepts pydantic-ai's ModelSettings.thinking key.

    Anthropic's Claude models all support extended thinking. OpenAI's
    classic Chat Completions models (gpt-4o, gpt-4o-mini — exactly what
    MODEL_FALLBACK/MODEL_BACKGROUND_FALLBACK default to) reject a
    reasoning_effort request outright, and do so specifically when tools
    are attached, which every conversation-agent call carries. Only
    OpenAI's reasoning-model families opt in.
    """
    if provider_for_model(model_name) == "anthropic":
        return True
    return model_name.startswith(("o1", "o3", "o4", "gpt-5"))


def _resolve_key(slot_key: str, model_name: str, s: Settings) -> str:
    """Return the per-slot key if set, else fall back to the matching global key."""
    if slot_key:
        return slot_key
    provider = provider_for_model(model_name)
    return s.anthropic_api_key if provider == "anthropic" else s.openai_api_key


def _make_model(model_name: str, api_key: str) -> Model:
    """Instantiate the correct provider model class for model_name."""
    if provider_for_model(model_name) == "anthropic":
        return AnthropicModel(model_name, provider=AnthropicProvider(api_key=api_key))
    return OpenAIChatModel(model_name, provider=OpenAIProvider(api_key=api_key))


class LLMRouter:
    def __init__(self, settings: Settings | None = None) -> None:
        self._s = settings or get_settings()

    def get_model(self, task_type: TaskType) -> Model:
        """Best single model for this task type.

        Used for initial agent construction and for logging. For runtime
        failover across providers on API failures, see get_model_chain().
        """
        return self.get_model_chain(task_type)[0]

    def get_model_chain(self, task_type: TaskType) -> list[Model]:
        """Ordered candidate models for this task type.

        Order: cheap background model (background task types only, when
        enabled), then primary, then fallback — skipping any slot without a
        resolvable API key. Callers that need runtime failover (e.g.
        agent_run) try these in order and move to the next candidate when
        one provider's API call fails.
        """
        s = self._s
        features = s.features
        chain: list[Model] = []

        if task_type in _BACKGROUND_TASK_TYPES and features.cheap_background_models:
            key = _resolve_key(s.model_background_api_key, s.model_background, s)
            if key:
                chain.append(_make_model(s.model_background, key))

        key = _resolve_key(s.model_primary_api_key, s.model_primary, s)
        if key:
            chain.append(_make_model(s.model_primary, key))

        if features.fallback_model:
            key = _resolve_key(s.model_fallback_api_key, s.model_fallback, s)
            if key:
                chain.append(_make_model(s.model_fallback, key))

        if not chain:
            raise RuntimeError(
                "No LLM provider configured. Set ANTHROPIC_API_KEY or OPENAI_API_KEY in .env"
            )
        return chain

    def get_thinking(self, task_type: TaskType, model_name: str) -> bool | str | None:
        """Configured reasoning/thinking level for this task type, if any,
        gated by whether `model_name` actually supports it.

        Returns None when unset, unparseable, or unsupported by the given
        model — callers should omit the `thinking` key entirely in that
        case rather than pass None through to pydantic-ai (which is itself
        a valid-but-different setting). The model check matters because a
        run can fail over to a different model than the one the setting was
        tuned for (see app/agent/runner.py's cross-provider chain) — sending
        e.g. `reasoning_effort` to gpt-4o gets rejected outright.
        """
        field = _THINKING_SETTINGS_FIELD.get(task_type)
        if field is None:
            return None
        raw = getattr(self._s, field, "")
        parsed = parse_thinking_level(raw)
        if parsed is None or not _model_supports_thinking(model_name):
            return None
        return parsed

    def get_model_settings(self, task_type: TaskType, model: Model) -> "ModelSettings | None":
        """model_settings override for task types with no other per-call
        settings to merge (the three background extraction/summarization
        agents). The conversation agent builds its own richer settings in
        app/agent/agent.py._build_model_settings() and calls get_thinking()
        directly instead.
        """
        thinking = self.get_thinking(task_type, model.model_name)
        if thinking is None:
            return None
        return cast("ModelSettings", {"thinking": thinking})
