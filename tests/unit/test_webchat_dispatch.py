"""Unit tests for app.webchat.dispatch — web chat inbound message handling.

agent_run and slash-command dispatch are monkeypatched so these tests
exercise dispatch.py's own logic (rate limiting, slash-command shortcut,
confirm/cancel delegation) without touching the LLM or a real DB.
"""

from __future__ import annotations

import pytest

import app.bot as bot_module
import app.webchat.dispatch as dispatch
from app.webchat.session import SessionInfo


@pytest.fixture(autouse=True)
def clear_rate_cache() -> None:
    bot_module._user_call_times.clear()


@pytest.fixture(autouse=True)
def force_non_test_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """dispatch.handle_web_message skips rate limiting when settings.is_test
    or settings.is_development is True (matches app.bot's behavior) — both
    are read-only properties derived from app_env, so patch that instead."""
    settings = dispatch.get_settings()
    monkeypatch.setattr(settings, "app_env", "production")


@pytest.fixture(autouse=True)
def patch_names(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        dispatch, "_resolve_names", lambda user_id, household_id: ("Kristian", "The Home")
    )


def _session() -> SessionInfo:
    from datetime import datetime, timezone

    return SessionInfo(
        token="tok-1", user_id="user-1", household_id="hh-1", expires_at=datetime.now(timezone.utc)
    )


async def test_rate_limited_message_returns_friendly_error(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = dispatch.get_settings()
    monkeypatch.setattr(settings, "rate_limit_per_user_per_minute", 1)

    called = {"count": 0}

    async def _fake_run_with_status(**kwargs: object) -> object:
        called["count"] += 1
        from app.agent.runner import RunOutcome

        return RunOutcome(response="ok", success=True, duration_ms=1, run_id="run-1")

    monkeypatch.setattr(dispatch, "_run_with_status", _fake_run_with_status)
    monkeypatch.setattr("app.memory.conversation.save_message_pair", lambda *a, **k: None)
    monkeypatch.setattr(
        "app.homey.state_cache.update_snapshots_from_tool_calls", lambda *a, **k: None
    )

    session = _session()
    first = await dispatch.handle_web_message(session, "hello")
    second = await dispatch.handle_web_message(session, "hello again")

    assert first == "ok"
    assert "too quickly" in (second or "")
    assert called["count"] == 1


async def test_slash_command_short_circuits_before_agent_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    async def _fake_try_dispatch(text: str, **kwargs: object) -> str | None:
        calls.append(text)
        return "command handled"

    async def _fake_run_with_status(**kwargs: object) -> object:
        raise AssertionError("agent_run should not be called for a handled slash command")

    monkeypatch.setattr("app.commands.dispatcher.try_dispatch", _fake_try_dispatch)
    monkeypatch.setattr(dispatch, "_run_with_status", _fake_run_with_status)

    session = _session()
    response = await dispatch.handle_web_message(session, "/help")

    assert response == "command handled"
    assert calls == ["/help"]


async def test_slash_command_not_handled_falls_through_to_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake_try_dispatch(text: str, **kwargs: object) -> str | None:
        return None  # not a recognized command — falls through to the LLM

    monkeypatch.setattr("app.commands.dispatcher.try_dispatch", _fake_try_dispatch)
    monkeypatch.setattr(dispatch, "_run_with_status", _make_fake_run_with_status("agent replied"))
    monkeypatch.setattr("app.memory.conversation.save_message_pair", lambda *a, **k: None)
    monkeypatch.setattr(
        "app.homey.state_cache.update_snapshots_from_tool_calls", lambda *a, **k: None
    )

    session = _session()
    response = await dispatch.handle_web_message(session, "/not-a-real-command")

    assert response == "agent replied"


async def test_plain_message_calls_agent_and_saves_history(monkeypatch: pytest.MonkeyPatch) -> None:
    saved: list[tuple[str, str, str]] = []
    monkeypatch.setattr(dispatch, "_run_with_status", _make_fake_run_with_status("hi there"))
    monkeypatch.setattr(
        "app.memory.conversation.save_message_pair",
        lambda user_id, text, response: saved.append((user_id, text, response)),
    )
    monkeypatch.setattr(
        "app.homey.state_cache.update_snapshots_from_tool_calls", lambda *a, **k: None
    )

    session = _session()
    response = await dispatch.handle_web_message(session, "what's the weather")

    assert response == "hi there"
    assert saved == [("user-1", "what's the weather", "hi there")]


async def test_failed_run_outcome_skips_history_save(monkeypatch: pytest.MonkeyPatch) -> None:
    saved: list[object] = []
    monkeypatch.setattr(
        dispatch,
        "_run_with_status",
        _make_fake_run_with_status("sorry, something broke", success=False),
    )
    monkeypatch.setattr(
        "app.memory.conversation.save_message_pair", lambda *a, **k: saved.append((a, k))
    )

    session = _session()
    response = await dispatch.handle_web_message(session, "hello")

    assert response == "sorry, something broke"
    assert saved == []


async def test_handle_web_confirm_delegates_with_web_channel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    async def _fake_execute(
        token: str, user_id: str, channel_user_id: str, channel: str = "telegram"
    ) -> object:
        captured.update(
            token=token, user_id=user_id, channel_user_id=channel_user_id, channel=channel
        )
        return "result"

    monkeypatch.setattr(dispatch, "execute_pending_action", _fake_execute)

    session = _session()
    result = await dispatch.handle_web_confirm(session, "action-token")

    assert result == "result"
    assert captured == {
        "token": "action-token",
        "user_id": "user-1",
        "channel_user_id": "tok-1",
        "channel": "web",
    }


async def test_handle_web_cancel_delegates_with_requesting_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    async def _fake_cancel(token: str, user_id: str) -> object:
        captured.update(token=token, user_id=user_id)
        return "cancelled"

    monkeypatch.setattr(dispatch, "cancel_pending_action_for_user", _fake_cancel)

    session = _session()
    result = await dispatch.handle_web_cancel(session, "action-token")

    assert result == "cancelled"
    assert captured == {"token": "action-token", "user_id": "user-1"}


def _make_fake_run_with_status(response_text: str, success: bool = True):  # type: ignore[no-untyped-def]
    from app.agent.runner import RunOutcome

    async def _fake(**kwargs: object) -> object:
        return RunOutcome(response=response_text, success=success, duration_ms=1, run_id="run-1")

    return _fake
