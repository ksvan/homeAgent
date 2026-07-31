"""Tests for app/scheduler/jobs.py.

Covers fire_scheduled_prompt, resume_task, execute_homey_action, and send_reminder.

Strategy:
- app.db.users_session is monkeypatched to use an in-memory SQLite engine, so DB
  reads/writes work without touching any real file.
- agent_run is AsyncMock so the LLM path never fires.
- Heavy side-effect dependencies (channel, MCP server, delivery helpers, envelope
  builder, save_message_pair) are mocked.
- Tests assert: correct prompt text assembled, correct agent_run kwargs (trigger,
  save_history), overlap guard behavior, early-return paths, and DB state changes.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from dataclasses import dataclass, field
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlmodel import Session

from app.models.scheduled_prompts import ScheduledPrompt
from app.models.tasks import Task
from app.scheduler.jobs import (
    _running_prompts,
    execute_homey_action,
    fire_scheduled_prompt,
    resume_task,
    send_reminder,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db(monkeypatch: pytest.MonkeyPatch, in_memory_engine: object) -> Session:
    """Return a live Session AND monkeypatch app.db.users_session to use it."""

    @contextmanager  # type: ignore[misc]
    def _session():
        with Session(in_memory_engine) as s:  # type: ignore[arg-type]
            yield s

    monkeypatch.setattr("app.db.users_session", _session)
    with Session(in_memory_engine) as s:  # type: ignore[arg-type]
        yield s


@dataclass
class _RunOutcome:
    response: str = "Agent response."
    success: bool = True
    duration_ms: int = 10
    run_id: str = "test-run-id"
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    tool_calls: list[object] = field(default_factory=list)
    new_messages: list[object] = field(default_factory=list)


def _mock_channel() -> MagicMock:
    ch = MagicMock()
    ch.send_message = AsyncMock()
    return ch


def _make_scheduled_prompt(
    session: Session,
    *,
    name: str = "Test prompt",
    prompt: str = "Check the status.",
    enabled: bool = True,
    behavior_kind: str | None = None,
    is_one_shot: bool = False,
) -> ScheduledPrompt:
    sp = ScheduledPrompt(
        household_id="hh1",
        user_id="u1",
        channel_user_id="ch1",
        name=name,
        prompt=prompt,
        recurrence="once" if is_one_shot else "daily",
        time_of_day="08:00",
        enabled=enabled,
        behavior_kind=behavior_kind,
    )
    session.add(sp)
    session.commit()
    session.refresh(sp)
    return sp


def _make_task(
    session: Session,
    *,
    status: str = "AWAITING_RESUME",
    context: dict | None = None,
) -> Task:
    task = Task(
        household_id="hh1",
        user_id="u1",
        title="Test task",
        status=status,
        context=json.dumps(context or {}),
    )
    session.add(task)
    session.commit()
    session.refresh(task)
    return task




# ---------------------------------------------------------------------------
# fire_scheduled_prompt — overlap guard
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_overlap_guard_skips_inner_function(db: Session) -> None:
    """A prompt already running should not start a second concurrent run."""
    sp = _make_scheduled_prompt(db)
    _running_prompts.add(sp.id)
    try:
        with patch("app.scheduler.jobs._fire_scheduled_prompt_inner", new_callable=AsyncMock) as inner:
            await fire_scheduled_prompt(
                prompt_id=sp.id,
                user_id="u1",
                household_id="hh1",
                channel_user_id="ch1",
                prompt_text="Hello",
                name="test",
            )
            inner.assert_not_called()
    finally:
        _running_prompts.discard(sp.id)


# ---------------------------------------------------------------------------
# fire_scheduled_prompt — disabled prompt
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_disabled_prompt_skips_agent_run(db: Session) -> None:
    sp = _make_scheduled_prompt(db, enabled=False)
    with (
        patch("app.agent.runner.agent_run", new_callable=AsyncMock) as mock_run,
        patch("app.scheduler.delivery.evaluate_preflight", return_value=(True, None)),
        patch("app.scheduler.delivery.evaluate_postflight", return_value=("delivered", None)),
        patch("app.scheduler.delivery.record_run"),
        patch("app.scheduler.envelope.build_prompt_envelope", return_value="env"),
    ):
        await fire_scheduled_prompt(
            prompt_id=sp.id,
            user_id="u1",
            household_id="hh1",
            channel_user_id="ch1",
            prompt_text=sp.prompt,
            name=sp.name,
        )
        mock_run.assert_not_called()


# ---------------------------------------------------------------------------
# fire_scheduled_prompt — preflight skip
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_preflight_skip_suppresses_agent_run(db: Session) -> None:
    sp = _make_scheduled_prompt(db)
    with (
        patch("app.agent.runner.agent_run", new_callable=AsyncMock) as mock_run,
        patch("app.agent.runner.get_user_run_lock"),
        patch("app.channels.registry.get_channel"),
        patch("app.scheduler.delivery.evaluate_preflight", return_value=(False, "quiet_hours")),
        patch("app.scheduler.delivery.evaluate_postflight", return_value=("delivered", None)),
        patch("app.scheduler.delivery.record_run"),
        patch("app.scheduler.envelope.build_prompt_envelope", return_value="envelope"),
    ):
        await fire_scheduled_prompt(
            prompt_id=sp.id,
            user_id="u1",
            household_id="hh1",
            channel_user_id="ch1",
            prompt_text=sp.prompt,
            name=sp.name,
        )
        mock_run.assert_not_called()


# ---------------------------------------------------------------------------
# fire_scheduled_prompt — agent called with correct kwargs
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_agent_called_with_scheduled_prompt_trigger(db: Session) -> None:
    sp = _make_scheduled_prompt(db)
    mock_lock = MagicMock()
    mock_lock.__aenter__ = AsyncMock(return_value=None)
    mock_lock.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("app.agent.runner.agent_run", new_callable=AsyncMock, return_value=_RunOutcome()) as mock_run,
        patch("app.agent.runner.get_user_run_lock", return_value=mock_lock),
        patch("app.channels.registry.get_channel", return_value=_mock_channel()),
        patch("app.memory.conversation.save_message_pair"),
        patch("app.scheduler.delivery.evaluate_preflight", return_value=(True, None)),
        patch("app.scheduler.delivery.evaluate_postflight", return_value=("delivered", None)),
        patch("app.scheduler.delivery.record_run"),
        patch("app.scheduler.envelope.build_prompt_envelope", return_value="[envelope] Check."),
    ):
        await fire_scheduled_prompt(
            prompt_id=sp.id,
            user_id="u1",
            household_id="hh1",
            channel_user_id="ch1",
            prompt_text=sp.prompt,
            name=sp.name,
        )

    mock_run.assert_called_once()
    kwargs = mock_run.call_args.kwargs
    assert kwargs["trigger"] == "scheduled_prompt"
    assert kwargs["save_history"] is False
    assert kwargs["user_id"] == "u1"
    assert kwargs["household_id"] == "hh1"
    assert kwargs["text"] == "[envelope] Check."


@pytest.mark.asyncio
async def test_lock_acquired_with_correct_user_id_for_scheduled_prompt(db: Session) -> None:
    sp = _make_scheduled_prompt(db)
    mock_lock = MagicMock()
    mock_lock.__aenter__ = AsyncMock(return_value=None)
    mock_lock.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("app.agent.runner.agent_run", new_callable=AsyncMock, return_value=_RunOutcome()),
        patch("app.agent.runner.get_user_run_lock", return_value=mock_lock) as mock_get_lock,
        patch("app.channels.registry.get_channel", return_value=_mock_channel()),
        patch("app.memory.conversation.save_message_pair"),
        patch("app.scheduler.delivery.evaluate_preflight", return_value=(True, None)),
        patch("app.scheduler.delivery.evaluate_postflight", return_value=("delivered", None)),
        patch("app.scheduler.delivery.record_run"),
        patch("app.scheduler.envelope.build_prompt_envelope", return_value="env"),
    ):
        await fire_scheduled_prompt(
            prompt_id=sp.id,
            user_id="u1",
            household_id="hh1",
            channel_user_id="ch1",
            prompt_text=sp.prompt,
            name=sp.name,
        )

    mock_get_lock.assert_called_once_with("u1")


# ---------------------------------------------------------------------------
# fire_scheduled_prompt — one-shot deletion
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_one_shot_prompt_deleted_after_firing(
    db: Session, in_memory_engine: object
) -> None:
    sp = _make_scheduled_prompt(db, is_one_shot=True)
    mock_lock = MagicMock()
    mock_lock.__aenter__ = AsyncMock(return_value=None)
    mock_lock.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("app.agent.runner.agent_run", new_callable=AsyncMock, return_value=_RunOutcome()),
        patch("app.agent.runner.get_user_run_lock", return_value=mock_lock),
        patch("app.channels.registry.get_channel", return_value=_mock_channel()),
        patch("app.memory.conversation.save_message_pair"),
        patch("app.scheduler.delivery.evaluate_preflight", return_value=(True, None)),
        patch("app.scheduler.delivery.evaluate_postflight", return_value=("delivered", None)),
        patch("app.scheduler.delivery.record_run"),
        patch("app.scheduler.envelope.build_prompt_envelope", return_value="env"),
    ):
        await fire_scheduled_prompt(
            prompt_id=sp.id,
            user_id="u1",
            household_id="hh1",
            channel_user_id="ch1",
            prompt_text=sp.prompt,
            name=sp.name,
            is_one_shot=True,
        )

    # Row should be gone after the one-shot fires — use a fresh session to
    # avoid hitting the stale identity map in the fixture's session.
    with Session(in_memory_engine) as fresh:  # type: ignore[arg-type]
        remaining = fresh.get(ScheduledPrompt, sp.id)
    assert remaining is None


# ---------------------------------------------------------------------------
# fire_scheduled_prompt — postflight skip suppresses delivery
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_postflight_skip_suppresses_channel_delivery(db: Session) -> None:
    sp = _make_scheduled_prompt(db)
    mock_lock = MagicMock()
    mock_lock.__aenter__ = AsyncMock(return_value=None)
    mock_lock.__aexit__ = AsyncMock(return_value=False)
    mock_ch = _mock_channel()

    with (
        patch("app.agent.runner.agent_run", new_callable=AsyncMock, return_value=_RunOutcome()),
        patch("app.agent.runner.get_user_run_lock", return_value=mock_lock),
        patch("app.channels.registry.get_channel", return_value=mock_ch),
        patch("app.memory.conversation.save_message_pair"),
        patch("app.scheduler.delivery.evaluate_preflight", return_value=(True, None)),
        patch("app.scheduler.delivery.evaluate_postflight", return_value=("skipped", "unchanged")),
        patch("app.scheduler.delivery.record_run"),
        patch("app.scheduler.envelope.build_prompt_envelope", return_value="env"),
    ):
        await fire_scheduled_prompt(
            prompt_id=sp.id,
            user_id="u1",
            household_id="hh1",
            channel_user_id="ch1",
            prompt_text=sp.prompt,
            name=sp.name,
        )

    mock_ch.send_message.assert_not_called()


# ---------------------------------------------------------------------------
# resume_task — early return paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_terminal_task_skips_agent_run(db: Session) -> None:
    task = _make_task(db, status="COMPLETED")
    with patch("app.agent.runner.agent_run", new_callable=AsyncMock) as mock_run:
        await resume_task(
            task_id=task.id,
            user_id="u1",
            household_id="hh1",
            channel_user_id="ch1",
        )
        mock_run.assert_not_called()


@pytest.mark.asyncio
async def test_active_task_skips_agent_run(db: Session) -> None:
    """Tasks in ACTIVE status are not resumable — wrong status."""
    task = _make_task(db, status="ACTIVE")
    with patch("app.agent.runner.agent_run", new_callable=AsyncMock) as mock_run:
        await resume_task(
            task_id=task.id,
            user_id="u1",
            household_id="hh1",
            channel_user_id="ch1",
        )
        mock_run.assert_not_called()


@pytest.mark.asyncio
async def test_nonexistent_task_skips_agent_run(db: Session) -> None:
    with patch("app.agent.runner.agent_run", new_callable=AsyncMock) as mock_run:
        await resume_task(
            task_id="does-not-exist",
            user_id="u1",
            household_id="hh1",
            channel_user_id="ch1",
        )
        mock_run.assert_not_called()


# ---------------------------------------------------------------------------
# resume_task — correct agent kwargs and prompt construction
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resume_task_calls_agent_with_correct_trigger(db: Session) -> None:
    task = _make_task(db, status="AWAITING_RESUME")
    mock_lock = MagicMock()
    mock_lock.__aenter__ = AsyncMock(return_value=None)
    mock_lock.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("app.agent.runner.agent_run", new_callable=AsyncMock, return_value=_RunOutcome()) as mock_run,
        patch("app.agent.runner.get_user_run_lock", return_value=mock_lock),
        patch("app.channels.registry.get_channel", return_value=_mock_channel()),
    ):
        await resume_task(
            task_id=task.id,
            user_id="u1",
            household_id="hh1",
            channel_user_id="ch1",
        )

    mock_run.assert_called_once()
    kwargs = mock_run.call_args.kwargs
    assert kwargs["trigger"] == "task_resume"
    assert kwargs["save_history"] is True
    assert kwargs["user_id"] == "u1"
    assert kwargs["household_id"] == "hh1"


@pytest.mark.asyncio
async def test_resume_task_lock_acquired_with_correct_user_id(db: Session) -> None:
    task = _make_task(db, status="AWAITING_RESUME")
    mock_lock = MagicMock()
    mock_lock.__aenter__ = AsyncMock(return_value=None)
    mock_lock.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("app.agent.runner.agent_run", new_callable=AsyncMock, return_value=_RunOutcome()),
        patch("app.agent.runner.get_user_run_lock", return_value=mock_lock) as mock_get_lock,
        patch("app.channels.registry.get_channel", return_value=_mock_channel()),
    ):
        await resume_task(
            task_id=task.id,
            user_id="u1",
            household_id="hh1",
            channel_user_id="ch1",
        )

    mock_get_lock.assert_called_once_with("u1")


@pytest.mark.asyncio
async def test_resume_prompt_contains_task_id(db: Session) -> None:
    task = _make_task(db, status="AWAITING_RESUME")
    mock_lock = MagicMock()
    mock_lock.__aenter__ = AsyncMock(return_value=None)
    mock_lock.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("app.agent.runner.agent_run", new_callable=AsyncMock, return_value=_RunOutcome()) as mock_run,
        patch("app.agent.runner.get_user_run_lock", return_value=mock_lock),
        patch("app.channels.registry.get_channel", return_value=_mock_channel()),
    ):
        await resume_task(
            task_id=task.id,
            user_id="u1",
            household_id="hh1",
            channel_user_id="ch1",
        )

    prompt = mock_run.call_args.kwargs["text"]
    assert task.id in prompt
    assert "Task resume" in prompt


@pytest.mark.asyncio
async def test_resume_prompt_includes_goal_contract(db: Session) -> None:
    ctx = {
        "goal": {
            "intent": "Book the cheapest flight",
            "success_criteria": "Flight booked",
            "acceptance_test": "Confirmation email received",
        }
    }
    task = _make_task(db, status="AWAITING_RESUME", context=ctx)
    mock_lock = MagicMock()
    mock_lock.__aenter__ = AsyncMock(return_value=None)
    mock_lock.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("app.agent.runner.agent_run", new_callable=AsyncMock, return_value=_RunOutcome()) as mock_run,
        patch("app.agent.runner.get_user_run_lock", return_value=mock_lock),
        patch("app.channels.registry.get_channel", return_value=_mock_channel()),
    ):
        await resume_task(
            task_id=task.id,
            user_id="u1",
            household_id="hh1",
            channel_user_id="ch1",
        )

    prompt = mock_run.call_args.kwargs["text"]
    assert "Book the cheapest flight" in prompt
    assert "Flight booked" in prompt
    assert "Confirmation email received" in prompt


@pytest.mark.asyncio
async def test_resume_prompt_includes_reason_and_observation(db: Session) -> None:
    ctx = {
        "pursuit": {
            "resume": {
                "reason": "Waiting for price drop",
                "expected_observation": "Price below 500 NOK",
            },
            "next_action": "check_prices",
            "attempt_count": 2,
            "max_attempts": 5,
        }
    }
    task = _make_task(db, status="AWAITING_RESUME", context=ctx)
    mock_lock = MagicMock()
    mock_lock.__aenter__ = AsyncMock(return_value=None)
    mock_lock.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("app.agent.runner.agent_run", new_callable=AsyncMock, return_value=_RunOutcome()) as mock_run,
        patch("app.agent.runner.get_user_run_lock", return_value=mock_lock),
        patch("app.channels.registry.get_channel", return_value=_mock_channel()),
    ):
        await resume_task(
            task_id=task.id,
            user_id="u1",
            household_id="hh1",
            channel_user_id="ch1",
        )

    prompt = mock_run.call_args.kwargs["text"]
    assert "Waiting for price drop" in prompt
    assert "Price below 500 NOK" in prompt
    assert "check_prices" in prompt
    assert "2 / 5" in prompt


@pytest.mark.asyncio
async def test_resume_task_transitions_task_to_active(
    db: Session, in_memory_engine: object
) -> None:
    """Task must be ACTIVE before agent_run is called."""
    task = _make_task(db, status="AWAITING_RESUME")
    status_at_call: list[str] = []

    async def _capture_run(**kwargs: object) -> _RunOutcome:
        # Open a fresh session to bypass the fixture session's identity map cache.
        with Session(in_memory_engine) as fresh:  # type: ignore[arg-type]
            reloaded = fresh.get(Task, task.id)
            if reloaded:
                status_at_call.append(reloaded.status)
        return _RunOutcome()

    mock_lock = MagicMock()
    mock_lock.__aenter__ = AsyncMock(return_value=None)
    mock_lock.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("app.agent.runner.agent_run", side_effect=_capture_run),
        patch("app.agent.runner.get_user_run_lock", return_value=mock_lock),
        patch("app.channels.registry.get_channel", return_value=_mock_channel()),
    ):
        await resume_task(
            task_id=task.id,
            user_id="u1",
            household_id="hh1",
            channel_user_id="ch1",
        )

    assert status_at_call == ["ACTIVE"]


# ---------------------------------------------------------------------------
# execute_homey_action — logic paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_mcp_server_notifies_user(db: Session) -> None:
    mock_ch = _mock_channel()

    with (
        patch("app.homey.mcp_client.get_mcp_server", return_value=None),
        patch("app.channels.registry.get_channel", return_value=mock_ch),
        patch("app.memory.conversation.save_message_pair"),
    ):
        await execute_homey_action(
            task_id="t1",
            user_id="u1",
            channel_user_id="ch1",
            tool_name="set_light",
            tool_args_json='{"device_id": "d1", "on": true}',
            description="Turn on the light",
        )

    mock_ch.send_message.assert_called_once()
    msg: str = mock_ch.send_message.call_args.args[1]
    assert "not connected" in msg.lower() or "Homey" in msg


@pytest.mark.asyncio
async def test_policy_block_notifies_user(db: Session) -> None:
    mock_ch = _mock_channel()
    mock_server = MagicMock()
    mock_decision = MagicMock()
    mock_decision.requires_confirm = True
    mock_decision.policy_name = "require_confirm"

    with (
        patch("app.homey.mcp_client.get_mcp_server", return_value=mock_server),
        patch("app.policy.gate.evaluate_policy", return_value=mock_decision),
        patch("app.channels.registry.get_channel", return_value=mock_ch),
        patch("app.memory.conversation.save_message_pair"),
    ):
        await execute_homey_action(
            task_id="t1",
            user_id="u1",
            channel_user_id="ch1",
            tool_name="set_light",
            tool_args_json='{"device_id": "d1", "on": true}',
            description="Turn on the light",
        )

    mock_ch.send_message.assert_called_once()
    msg: str = mock_ch.send_message.call_args.args[1]
    assert "block" in msg.lower() or "confirm" in msg.lower()
    mock_server.direct_call_tool.assert_not_called()


@pytest.mark.asyncio
async def test_successful_action_calls_direct_call_tool_with_stripped_name(db: Session) -> None:
    mock_ch = _mock_channel()
    mock_server = MagicMock()
    mock_server.direct_call_tool = AsyncMock(return_value="ok")
    mock_decision = MagicMock()
    mock_decision.requires_confirm = False

    with (
        patch("app.homey.mcp_client.get_mcp_server", return_value=mock_server),
        patch("app.policy.gate.evaluate_policy", return_value=mock_decision),
        patch("app.channels.registry.get_channel", return_value=mock_ch),
        patch("app.memory.conversation.save_message_pair"),
    ):
        await execute_homey_action(
            task_id="t1",
            user_id="u1",
            channel_user_id="ch1",
            tool_name="homey_set_light",  # prefixed name as stored in task
            tool_args_json='{"device_id": "d1", "on": true}',
            description="Turn on the light",
        )

    # homey_ prefix must be stripped before calling the MCP tool
    mock_server.direct_call_tool.assert_called_once_with(
        "set_light", {"device_id": "d1", "on": True}
    )

    msg: str = mock_ch.send_message.call_args.args[1]
    assert "Done" in msg or "✅" in msg


# ---------------------------------------------------------------------------
# send_reminder — delivery and DB state
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reminder_sends_formatted_message(db: Session) -> None:
    task = _make_task(db, status="ACTIVE")
    mock_ch = _mock_channel()

    with (
        patch("app.channels.registry.get_channel", return_value=mock_ch),
        patch("app.memory.conversation.save_message_pair"),
    ):
        await send_reminder(
            task_id=task.id,
            user_id="u1",
            channel_user_id="ch1",
            text="Call the dentist",
        )

    mock_ch.send_message.assert_called_once()
    msg: str = mock_ch.send_message.call_args.args[1]
    assert "Call the dentist" in msg
    assert "Reminder" in msg


@pytest.mark.asyncio
async def test_reminder_marks_task_completed(db: Session) -> None:
    task = _make_task(db, status="ACTIVE")
    mock_ch = _mock_channel()

    with (
        patch("app.channels.registry.get_channel", return_value=mock_ch),
        patch("app.memory.conversation.save_message_pair"),
    ):
        await send_reminder(
            task_id=task.id,
            user_id="u1",
            channel_user_id="ch1",
            text="Call the dentist",
        )

    db.expire(task)
    db.refresh(task)
    assert task.status == "COMPLETED"
    assert task.completed_at is not None


@pytest.mark.asyncio
async def test_reminder_with_no_channel_does_not_raise(db: Session) -> None:
    task = _make_task(db, status="ACTIVE")

    with (
        patch("app.channels.registry.get_channel", return_value=None),
        patch("app.memory.conversation.save_message_pair"),
    ):
        # Should complete without raising even if channel is unavailable
        await send_reminder(
            task_id=task.id,
            user_id="u1",
            channel_user_id="ch1",
            text="Pick up kids",
        )
