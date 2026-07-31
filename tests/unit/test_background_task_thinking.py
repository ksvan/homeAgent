"""Tests confirming the three background LLM call sites thread model_settings
(specifically the configured thinking level) through to agent.run().

See app/agent/llm_router.py LLMRouter.get_model_settings() and
docs — extract_and_store_memories() / maybe_summarize_conversation() /
extract_and_propose_world_updates() previously called .run(text) with no
settings at all.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Generator
from unittest.mock import AsyncMock, patch

import pytest
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart
from sqlmodel import Session

from app.models.memory import ConversationMessage

_SENTINEL_SETTINGS = {"thinking": "low"}


class _FakeOutput:
    def __init__(self, value: object) -> None:
        self.output = value


async def test_memory_extraction_passes_configured_model_settings() -> None:
    from app.memory.extraction import extract_and_store_memories

    fake_run = AsyncMock(return_value=_FakeOutput(type("Facts", (), {"facts": []})()))
    fake_agent = type("FakeAgent", (), {"run": fake_run})()

    messages = [
        ModelRequest(parts=[UserPromptPart(content="Remind me to water the plants weekly")]),
        ModelResponse(parts=[TextPart(content="Got it, I'll remember that.")]),
    ]

    with (
        patch("app.memory.extraction._get_extractor", return_value=fake_agent),
        patch(
            "app.agent.llm_router.LLMRouter.get_model_settings",
            return_value=_SENTINEL_SETTINGS,
        ),
    ):
        await extract_and_store_memories(
            household_id="hh1", user_id="u1", run_id="run1", new_messages=messages
        )

    fake_run.assert_awaited_once()
    assert fake_run.call_args.kwargs["model_settings"] == _SENTINEL_SETTINGS


async def test_memory_extraction_passes_none_when_unconfigured() -> None:
    from app.memory.extraction import extract_and_store_memories

    fake_run = AsyncMock(return_value=_FakeOutput(type("Facts", (), {"facts": []})()))
    fake_agent = type("FakeAgent", (), {"run": fake_run})()
    messages = [ModelRequest(parts=[UserPromptPart(content="hello")])]

    with (
        patch("app.memory.extraction._get_extractor", return_value=fake_agent),
        patch("app.agent.llm_router.LLMRouter.get_model_settings", return_value=None),
    ):
        await extract_and_store_memories(
            household_id="hh1", user_id="u1", run_id="run1", new_messages=messages
        )

    assert fake_run.call_args.kwargs["model_settings"] is None


async def test_world_model_extraction_passes_configured_model_settings() -> None:
    from app.world.extraction import extract_and_propose_world_updates

    fake_run = AsyncMock(return_value=_FakeOutput(type("Proposals", (), {"proposals": []})()))
    fake_agent = type("FakeAgent", (), {"run": fake_run})()
    messages = [ModelRequest(parts=[UserPromptPart(content="The oven is in the kitchen")])]

    with (
        patch("app.world.extraction._get_extractor", return_value=fake_agent),
        patch(
            "app.agent.llm_router.LLMRouter.get_model_settings",
            return_value=_SENTINEL_SETTINGS,
        ),
    ):
        await extract_and_propose_world_updates(
            household_id="hh1", user_id="u1", run_id="run1", new_messages=messages
        )

    fake_run.assert_awaited_once()
    assert fake_run.call_args.kwargs["model_settings"] == _SENTINEL_SETTINGS


@pytest.fixture
def memory_db(monkeypatch: pytest.MonkeyPatch, in_memory_engine: object) -> Session:
    """Monkeypatch app.memory.conversation's already-imported memory_session
    name (imported at module load, not lazily) to use the in-memory engine.
    """

    @contextmanager
    def _session() -> Generator[Session, None, None]:
        with Session(in_memory_engine) as s:  # type: ignore[arg-type]
            yield s

    monkeypatch.setattr("app.memory.conversation.memory_session", _session)
    with Session(in_memory_engine) as s:  # type: ignore[arg-type]
        yield s


async def test_summarization_passes_configured_model_settings(memory_db: Session) -> None:
    from app.memory.conversation import maybe_summarize_conversation

    # Seed enough messages to cross _SUMMARY_THRESHOLD (20).
    now = datetime.now(timezone.utc)
    for i in range(25):
        memory_db.add(
            ConversationMessage(user_id="u1", role="user", content=f"message {i}", created_at=now)
        )
    memory_db.commit()

    fake_run = AsyncMock(return_value=_FakeOutput("summary text"))
    fake_agent = type("FakeAgent", (), {"run": fake_run})()

    with (
        patch("app.memory.conversation._get_summarizer", return_value=fake_agent),
        patch(
            "app.agent.llm_router.LLMRouter.get_model_settings",
            return_value=_SENTINEL_SETTINGS,
        ),
    ):
        await maybe_summarize_conversation("u1")

    fake_run.assert_awaited_once()
    assert fake_run.call_args.kwargs["model_settings"] == _SENTINEL_SETTINGS
