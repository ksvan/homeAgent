"""Unit tests for app.agent.runner agent_run() — provider failover and
user-facing error classification on API failures.

run_conversation and the model chain are mocked; the goal is to verify the
control flow in agent_run() itself (which model is tried, in what order,
and what response/reason is produced on failure), not the LLM call.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic_ai.exceptions import ModelHTTPError

from app.agent.runner import agent_run


@dataclass
class _FakeUsage:
    input_tokens: int = 10
    output_tokens: int = 5
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0


@dataclass
class _FakeResultObj:
    """Stand-in for pydantic-ai's AgentRunResult — usage is an attribute."""

    output: str = "ok"
    usage: _FakeUsage = field(default_factory=_FakeUsage)

    def new_messages(self) -> list[object]:
        return []


def _model(name: str) -> MagicMock:
    m = MagicMock()
    m.__str__.return_value = name
    return m


@pytest.fixture(autouse=True)
def _patch_common(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.agent import context as context_mod
    from app.agent import runner as runner_mod

    monkeypatch.setattr(
        context_mod, "assemble_context", lambda *a, **kw: context_mod.AgentContext()
    )
    monkeypatch.setattr(runner_mod, "_write_run_log", lambda **kw: None)
    monkeypatch.setattr("app.control.events.emit", lambda *a, **kw: None)

    class _NullSession:
        def __enter__(self) -> "_NullSession":
            return self

        def __exit__(self, *exc: object) -> None:
            return None

        def get(self, *a: object, **kw: object) -> None:
            return None

    monkeypatch.setattr("app.db.users_session", lambda: _NullSession())


async def _run(**kwargs: object) -> object:
    return await agent_run(
        text="hello",
        user_id="u1",
        household_id="h1",
        channel_user_id="tg:1",
        user_name="Alice",
        household_name="Casa",
        **kwargs,
    )


async def test_success_on_primary_model_uses_no_failover() -> None:
    primary = _model("primary-model")
    with (
        patch("app.agent.llm_router.LLMRouter.get_model_chain", return_value=[primary]),
        patch("app.agent.agent.run_conversation", new=AsyncMock(return_value=_FakeResultObj())),
    ):
        outcome = await _run()

    assert outcome.success is True
    assert outcome.response == "ok"


async def test_cache_tokens_are_extracted_into_run_outcome() -> None:
    # See docs/prompt-caching-design.md — cache_read/cache_write must flow
    # from pydantic-ai's usage object into RunOutcome, not just input/output.
    primary = _model("primary-model")
    usage = _FakeUsage(
        input_tokens=100, output_tokens=20, cache_read_tokens=850, cache_write_tokens=0
    )
    result = _FakeResultObj(output="cached response", usage=usage)
    with (
        patch("app.agent.llm_router.LLMRouter.get_model_chain", return_value=[primary]),
        patch("app.agent.agent.run_conversation", new=AsyncMock(return_value=result)),
    ):
        outcome = await _run()

    assert outcome.success is True
    assert outcome.cache_read_tokens == 850
    assert outcome.cache_write_tokens == 0
    assert outcome.input_tokens == 100


async def test_zero_cache_tokens_when_usage_has_none() -> None:
    primary = _model("primary-model")
    with (
        patch("app.agent.llm_router.LLMRouter.get_model_chain", return_value=[primary]),
        patch("app.agent.agent.run_conversation", new=AsyncMock(return_value=_FakeResultObj())),
    ):
        outcome = await _run()

    assert outcome.cache_read_tokens == 0
    assert outcome.cache_write_tokens == 0


async def test_auth_error_on_primary_fails_over_to_fallback() -> None:
    primary = _model("primary-model")
    fallback = _model("fallback-model")
    call_log: list[object] = []

    async def _fake_run_conversation(*args: object, **kwargs: object) -> object:
        model = kwargs.get("model")
        call_log.append(model)
        if model is primary:
            raise ModelHTTPError(status_code=401, model_name="primary-model")
        return _FakeResultObj(output="from fallback")

    fake_run = AsyncMock(side_effect=_fake_run_conversation)
    with (
        patch("app.agent.llm_router.LLMRouter.get_model_chain", return_value=[primary, fallback]),
        patch("app.agent.agent.run_conversation", new=fake_run),
    ):
        outcome = await _run()

    assert outcome.success is True
    assert outcome.response == "from fallback"
    # Primary tried exactly once (no wasted retries against an auth failure),
    # then fallback tried once.
    assert call_log == [primary, fallback]


async def test_auth_error_on_all_models_returns_config_error_message() -> None:
    primary = _model("primary-model")

    async def _always_401(*args: object, **kwargs: object) -> object:
        raise ModelHTTPError(status_code=401, model_name="primary-model")

    with (
        patch("app.agent.llm_router.LLMRouter.get_model_chain", return_value=[primary]),
        patch("app.agent.agent.run_conversation", new=AsyncMock(side_effect=_always_401)),
    ):
        outcome = await _run()

    assert outcome.success is False
    assert "API key" in outcome.response


async def test_rate_limit_retries_same_model_before_giving_up() -> None:
    primary = _model("primary-model")
    attempts: list[int] = []

    async def _always_429(*args: object, **kwargs: object) -> object:
        attempts.append(1)
        raise ModelHTTPError(status_code=429, model_name="primary-model")

    with (
        patch("app.agent.llm_router.LLMRouter.get_model_chain", return_value=[primary]),
        patch("app.agent.agent.run_conversation", new=AsyncMock(side_effect=_always_429)),
        patch("asyncio.sleep", new=AsyncMock(return_value=None)),
    ):
        outcome = await _run(retries=2)

    assert outcome.success is False
    assert len(attempts) == 3  # initial + 2 retries, same model
    assert "rate-limited" in outcome.response


async def test_no_provider_configured_returns_config_error() -> None:
    with patch("app.agent.llm_router.LLMRouter.get_model_chain", return_value=[]):
        outcome = await _run()

    assert outcome.success is False
    assert "API key" in outcome.response
