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
    EMBEDDING = "EMBEDDING"


class LLMRouter:
    def __init__(self, settings: Settings | None = None) -> None:
        self._s = settings or get_settings()

    def get_model(self, task_type: TaskType) -> Model:
        s = self._s
        features = s.features

        # Background tasks → cheap model when feature is enabled
        if task_type in (TaskType.MEMORY_EXTRACTION, TaskType.SUMMARIZATION):
            if features.cheap_background_models and s.anthropic_api_key:
                return AnthropicModel(
                    s.model_background,
                    provider=AnthropicProvider(api_key=s.anthropic_api_key),
                )

        # Primary: Anthropic
        if s.anthropic_api_key:
            return AnthropicModel(
                s.model_primary,
                provider=AnthropicProvider(api_key=s.anthropic_api_key),
            )

        # Fallback: OpenAI
        if features.fallback_model and s.openai_api_key:
            return OpenAIModel(
                s.model_fallback,
                provider=OpenAIProvider(api_key=s.openai_api_key),
            )

        raise RuntimeError(
            "No LLM provider configured. "
            "Set ANTHROPIC_API_KEY or OPENAI_API_KEY in .env"
        )
