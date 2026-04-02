from __future__ import annotations

from enum import Enum

from pydantic_ai.models import Model
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.models.openai import OpenAIModel
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


def _resolve_key(slot_key: str, model_name: str, s: Settings) -> str:
    """Return the per-slot key if set, else fall back to the matching global key."""
    if slot_key:
        return slot_key
    return s.anthropic_api_key if model_name.startswith("claude") else s.openai_api_key


def _make_model(model_name: str, api_key: str) -> Model:
    """Instantiate the correct provider model from the API key prefix."""
    if api_key.startswith("sk-ant-"):
        return AnthropicModel(model_name, provider=AnthropicProvider(api_key=api_key))
    return OpenAIModel(model_name, provider=OpenAIProvider(api_key=api_key))


class LLMRouter:
    def __init__(self, settings: Settings | None = None) -> None:
        self._s = settings or get_settings()

    def get_model(self, task_type: TaskType) -> Model:
        s = self._s
        features = s.features

        # Background tasks → cheap model when feature is enabled
        if task_type in (
            TaskType.MEMORY_EXTRACTION,
            TaskType.SUMMARIZATION,
            TaskType.WORLD_MODEL_EXTRACTION,
        ):
            if features.cheap_background_models:
                key = _resolve_key(s.model_background_api_key, s.model_background, s)
                if key:
                    return _make_model(s.model_background, key)

        # Primary model
        key = _resolve_key(s.model_primary_api_key, s.model_primary, s)
        if key:
            return _make_model(s.model_primary, key)

        # Fallback model
        if features.fallback_model:
            key = _resolve_key(s.model_fallback_api_key, s.model_fallback, s)
            if key:
                return _make_model(s.model_fallback, key)

        raise RuntimeError(
            "No LLM provider configured. "
            "Set ANTHROPIC_API_KEY or OPENAI_API_KEY in .env"
        )
