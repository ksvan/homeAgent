"""Unit tests for autonomous task pursuit state.

Covers: record_task_attempt logic, schedule_task_followup budget enforcement,
_render_full_task pursuit rendering, advance_task_step step outcome storage,
fail_task status transition and summary, and step result_note rendering.

Tests operate on repository and service layer directly without going through
the agent tool wrappers (which require RunContext).
"""
from __future__ import annotations

import json
from contextlib import contextmanager

import pytest
from sqlmodel import Session

from app.tasks.repository import TaskRepository
from app.tasks.service import _render_full_task


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def repo(monkeypatch: pytest.MonkeyPatch, in_memory_engine: object) -> TaskRepository:
    @contextmanager
    def _session():  # type: ignore[misc]
        with Session(in_memory_engine) as s:  # type: ignore[arg-type]
            yield s

    monkeypatch.setattr("app.tasks.repository.users_session", _session)
    return TaskRepository()


def _make_task(repo: TaskRepository, **kwargs: object) -> object:
    return repo.create_task(
        household_id="hh-1",
        user_id="user-1",
        title=str(kwargs.pop("title", "Test task")),
        **kwargs,  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------------------
# AWAITING_RESUME status transitions
# ---------------------------------------------------------------------------


def test_active_can_transition_to_awaiting_resume(repo: TaskRepository) -> None:
    task = _make_task(repo)
    updated = repo.transition_status(task.id, "AWAITING_RESUME")
    assert updated.status == "AWAITING_RESUME"


def test_awaiting_resume_can_transition_to_active(repo: TaskRepository) -> None:
    task = _make_task(repo)
    repo.transition_status(task.id, "AWAITING_RESUME")
    updated = repo.transition_status(task.id, "ACTIVE")
    assert updated.status == "ACTIVE"


def test_awaiting_resume_can_transition_to_failed(repo: TaskRepository) -> None:
    task = _make_task(repo)
    repo.transition_status(task.id, "AWAITING_RESUME")
    updated = repo.transition_status(task.id, "FAILED")
    assert updated.status == "FAILED"


def test_awaiting_resume_cannot_transition_to_completed(repo: TaskRepository) -> None:
    task = _make_task(repo)
    repo.transition_status(task.id, "AWAITING_RESUME")
    with pytest.raises(ValueError):
        repo.transition_status(task.id, "COMPLETED")


def test_awaiting_resume_is_included_in_active_tasks(repo: TaskRepository) -> None:
    task = _make_task(repo)
    repo.transition_status(task.id, "AWAITING_RESUME")
    active = repo.get_active_tasks("user-1")
    assert any(t.id == task.id for t in active)


# ---------------------------------------------------------------------------
# Pursuit context helpers (simulate what record_task_attempt does)
# ---------------------------------------------------------------------------


def _write_pursuit(repo: TaskRepository, task_id: str, pursuit: dict) -> None:
    task = repo.get_task(task_id)
    assert task is not None
    try:
        ctx = json.loads(task.context or "{}")
    except json.JSONDecodeError:
        ctx = {}
    ctx["pursuit"] = pursuit
    repo.update_task(task_id, context=json.dumps(ctx))


def _read_pursuit(repo: TaskRepository, task_id: str) -> dict:
    task = repo.get_task(task_id)
    assert task is not None
    try:
        ctx = json.loads(task.context or "{}")
    except json.JSONDecodeError:
        return {}
    return ctx.get("pursuit", {})


# ---------------------------------------------------------------------------
# Pursuit state storage and retrieval
# ---------------------------------------------------------------------------


def test_pursuit_context_round_trips(repo: TaskRepository) -> None:
    task = _make_task(repo)
    pursuit = {
        "attempt_count": 2,
        "max_attempts": 5,
        "current_approach": "Read device state",
        "last_attempt": {"result": "partial", "result_note": "No event yet"},
        "next_action": "Wait and check again",
        "recent_attempts": [],
    }
    _write_pursuit(repo, task.id, pursuit)
    stored = _read_pursuit(repo, task.id)
    assert stored["attempt_count"] == 2
    assert stored["current_approach"] == "Read device state"
    assert stored["last_attempt"]["result"] == "partial"


def test_pursuit_resume_intent_stored_and_cleared(repo: TaskRepository) -> None:
    task = _make_task(repo)
    pursuit = {
        "attempt_count": 1,
        "max_attempts": 5,
        "resume": {
            "reason": "Check if motion event arrived",
            "expected_observation": "Motion event from hallway sensor",
            "resume_at": "2026-04-24T12:30:00+02:00",
        },
    }
    _write_pursuit(repo, task.id, pursuit)
    stored = _read_pursuit(repo, task.id)
    assert stored["resume"]["reason"] == "Check if motion event arrived"
    assert stored["resume"]["expected_observation"] == "Motion event from hallway sensor"

    # Simulate clearing on resume (as resume_task() does)
    stored.pop("resume", None)
    _write_pursuit(repo, task.id, stored)
    after = _read_pursuit(repo, task.id)
    assert "resume" not in after


def test_recent_attempts_capped_at_five(repo: TaskRepository) -> None:
    task = _make_task(repo)
    # Simulate 7 attempts being recorded
    recent = []
    for i in range(7):
        recent.append({"result": "partial", "result_note": f"attempt {i}"})
        if len(recent) > 5:
            recent = recent[-5:]
    pursuit = {"attempt_count": 7, "max_attempts": 5, "recent_attempts": recent}
    _write_pursuit(repo, task.id, pursuit)
    stored = _read_pursuit(repo, task.id)
    assert len(stored["recent_attempts"]) == 5
    assert stored["recent_attempts"][0]["result_note"] == "attempt 2"


# ---------------------------------------------------------------------------
# Retry budget enforcement (simulated — mirrors schedule_task_followup logic)
# ---------------------------------------------------------------------------


def _budget_exhausted(pursuit: dict) -> bool:
    attempt_count = int(pursuit.get("attempt_count", 0))
    max_attempts = int(pursuit.get("max_attempts", 5))
    return attempt_count >= max_attempts


def test_budget_not_exhausted_below_limit() -> None:
    pursuit = {"attempt_count": 3, "max_attempts": 5}
    assert not _budget_exhausted(pursuit)


def test_budget_exhausted_at_limit() -> None:
    pursuit = {"attempt_count": 5, "max_attempts": 5}
    assert _budget_exhausted(pursuit)


def test_budget_exhausted_above_limit() -> None:
    pursuit = {"attempt_count": 6, "max_attempts": 5}
    assert _budget_exhausted(pursuit)


def test_budget_not_exhausted_with_no_attempts() -> None:
    pursuit = {}
    assert not _budget_exhausted(pursuit)


# ---------------------------------------------------------------------------
# _render_full_task — pursuit fields appear in output
# ---------------------------------------------------------------------------


def test_render_includes_current_approach(repo: TaskRepository) -> None:
    task = _make_task(repo)
    _write_pursuit(repo, task.id, {"attempt_count": 2, "current_approach": "Read Homey state"})
    output = _render_full_task(repo, repo.get_task(task.id))
    assert "current approach: Read Homey state" in output


def test_render_includes_attempt_count(repo: TaskRepository) -> None:
    task = _make_task(repo)
    _write_pursuit(repo, task.id, {"attempt_count": 3, "max_attempts": 5})
    output = _render_full_task(repo, repo.get_task(task.id))
    assert "attempts: 3 / 5" in output


def test_render_includes_last_attempt(repo: TaskRepository) -> None:
    task = _make_task(repo)
    _write_pursuit(repo, task.id, {
        "attempt_count": 1,
        "last_attempt": {"result": "partial", "result_note": "No event observed"},
    })
    output = _render_full_task(repo, repo.get_task(task.id))
    assert "last attempt: partial — No event observed" in output


def test_render_includes_next_action(repo: TaskRepository) -> None:
    task = _make_task(repo)
    _write_pursuit(repo, task.id, {"next_action": "Try reading device state directly"})
    output = _render_full_task(repo, repo.get_task(task.id))
    assert "next action: Try reading device state directly" in output


def test_render_includes_resume_reason_and_observation(repo: TaskRepository) -> None:
    task = _make_task(repo)
    _write_pursuit(repo, task.id, {
        "resume": {
            "reason": "Check for motion event",
            "expected_observation": "Motion event from hallway",
        }
    })
    output = _render_full_task(repo, repo.get_task(task.id))
    assert "resume reason: Check for motion event" in output
    assert "expected observation: Motion event from hallway" in output


def test_render_no_pursuit_section_when_empty(repo: TaskRepository) -> None:
    task = _make_task(repo)
    output = _render_full_task(repo, repo.get_task(task.id))
    assert "current approach" not in output
    assert "attempts:" not in output
    assert "last attempt" not in output


def test_render_skips_zero_attempt_count(repo: TaskRepository) -> None:
    task = _make_task(repo)
    _write_pursuit(repo, task.id, {"attempt_count": 0, "current_approach": "Start"})
    output = _render_full_task(repo, repo.get_task(task.id))
    assert "attempts:" not in output
    assert "current approach: Start" in output


# ---------------------------------------------------------------------------
# Phase 2: advance_task_step — step outcome storage via repository
# ---------------------------------------------------------------------------


def test_update_step_stores_result_note_in_details_json(repo: TaskRepository) -> None:
    task = _make_task(repo, steps=[{"title": "Check state", "step_type": "tool"}])
    steps = repo.get_steps(task.id)
    assert len(steps) == 1
    step = steps[0]
    repo.update_step(
        step.id,
        status="done",
        details_json=json.dumps({"result_note": "Device online, state confirmed"}),
    )
    refreshed = repo.get_steps(task.id)[0]
    details = json.loads(refreshed.details_json)
    assert details["result_note"] == "Device online, state confirmed"
    assert refreshed.status == "done"


def test_update_step_failed_status(repo: TaskRepository) -> None:
    task = _make_task(repo, steps=[{"title": "Run check", "step_type": "tool"}])
    step = repo.get_steps(task.id)[0]
    repo.update_step(
        step.id,
        status="failed",
        details_json=json.dumps({"result_note": "Tool timed out"}),
    )
    refreshed = repo.get_steps(task.id)[0]
    assert refreshed.status == "failed"
    assert json.loads(refreshed.details_json)["result_note"] == "Tool timed out"


def test_advance_step_activates_next_when_done(repo: TaskRepository) -> None:
    task = _make_task(
        repo,
        steps=[
            {"title": "Step A", "step_type": "research"},
            {"title": "Step B", "step_type": "tool"},
        ],
    )
    repo.advance_step(task.id, completed_step_index=0)
    steps = {s.step_index: s for s in repo.get_steps(task.id)}
    assert steps[0].status == "done"
    assert steps[1].status == "active"


# ---------------------------------------------------------------------------
# Phase 2: fail_task — FAILED transition and summary
# ---------------------------------------------------------------------------


def test_transition_to_failed_from_active(repo: TaskRepository) -> None:
    task = _make_task(repo)
    updated = repo.transition_status(task.id, "FAILED")
    assert updated.status == "FAILED"
    assert updated.completed_at is not None


def test_transition_to_failed_from_awaiting_resume(repo: TaskRepository) -> None:
    task = _make_task(repo)
    repo.transition_status(task.id, "AWAITING_RESUME")
    updated = repo.transition_status(task.id, "FAILED")
    assert updated.status == "FAILED"


def test_failed_task_summary_stored(repo: TaskRepository) -> None:
    task = _make_task(repo)
    repo.transition_status(task.id, "FAILED")
    repo.update_task(task.id, summary="Failed: retry budget exhausted after 5 attempts")
    reloaded = repo.get_task(task.id)
    assert reloaded is not None
    assert "retry budget exhausted" in (reloaded.summary or "")


def test_failed_task_excluded_from_active_tasks(repo: TaskRepository) -> None:
    task = _make_task(repo)
    repo.transition_status(task.id, "FAILED")
    active = repo.get_active_tasks("user-1")
    assert not any(t.id == task.id for t in active)


def test_failed_is_terminal_cannot_reactivate(repo: TaskRepository) -> None:
    task = _make_task(repo)
    repo.transition_status(task.id, "FAILED")
    with pytest.raises(ValueError):
        repo.transition_status(task.id, "ACTIVE")


# ---------------------------------------------------------------------------
# Phase 2: _render_full_task — step result_note appears in output
# ---------------------------------------------------------------------------


def test_render_step_result_note_appears_for_done_step(repo: TaskRepository) -> None:
    task = _make_task(
        repo,
        steps=[{"title": "Verify state", "step_type": "tool"}],
    )
    step = repo.get_steps(task.id)[0]
    repo.update_step(
        step.id,
        status="done",
        details_json=json.dumps({"result_note": "State verified successfully"}),
    )
    output = _render_full_task(repo, repo.get_task(task.id))
    assert "result: State verified successfully" in output


def test_render_step_result_note_appears_for_failed_step(repo: TaskRepository) -> None:
    task = _make_task(
        repo,
        steps=[{"title": "Call API", "step_type": "tool"}],
    )
    step = repo.get_steps(task.id)[0]
    repo.update_step(
        step.id,
        status="failed",
        details_json=json.dumps({"result_note": "Connection refused"}),
    )
    output = _render_full_task(repo, repo.get_task(task.id))
    assert "result: Connection refused" in output


def test_render_step_no_result_note_when_details_empty(repo: TaskRepository) -> None:
    task = _make_task(
        repo,
        steps=[{"title": "Pending step", "step_type": "research"}],
    )
    output = _render_full_task(repo, repo.get_task(task.id))
    assert "result:" not in output


def test_render_step_result_note_not_shown_when_no_note_key(repo: TaskRepository) -> None:
    task = _make_task(
        repo,
        steps=[{"title": "Some step", "step_type": "tool"}],
    )
    step = repo.get_steps(task.id)[0]
    repo.update_step(step.id, status="done", details_json=json.dumps({"other_key": "value"}))
    output = _render_full_task(repo, repo.get_task(task.id))
    assert "result:" not in output
