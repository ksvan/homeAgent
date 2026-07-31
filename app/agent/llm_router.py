from __future__ import annotations

from enum import Enum

from pydantic_ai.models import Model
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.anthropic import AnthropicProvider
from pydantic_ai.providers.openai import OpenAIProvider

from app.config import Settings, get_settings


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


def provider_for_model(model_name: str) -> str:
    """Return "anthropic" or "openai" for a given model name.

    Single source of truth for provider detection. Both API key resolution
    and model instantiation must use this so a model name and the provider
    it's actually sent to can never disagree.
    """
    return "anthropic" if model_name.startswith("claude") else "openai"


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
