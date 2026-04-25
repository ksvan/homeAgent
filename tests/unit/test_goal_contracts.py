"""Unit tests for Phase 5: Goal Contracts.

Tests operate on repository and service layers directly, without going through
agent tool wrappers (which require RunContext). Goal state is stored in
Task.context["goal"] JSON.
"""
from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime, timezone

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


def _set_goal(repo: TaskRepository, task_id: str, **goal_fields: object) -> None:
    """Helper: write a context.goal blob onto a task."""
    task = repo.get_task(task_id)
    assert task is not None
    try:
        ctx: dict[str, object] = json.loads(task.context or "{}")
    except json.JSONDecodeError:
        ctx = {}
    ctx["goal"] = {
        "intent": goal_fields.get("intent", "test intent"),
        "success_criteria": goal_fields.get("success_criteria", ""),
        "acceptance_test": goal_fields.get("acceptance_test", ""),
        "outcome": goal_fields.get("outcome", None),
        "completion_rejection_count": int(goal_fields.get("completion_rejection_count", 0)),
    }
    repo.update_task(task_id, context=json.dumps(ctx))


# ---------------------------------------------------------------------------
# 1. Goal contract stored at task creation
# ---------------------------------------------------------------------------


def test_goal_contract_stored_when_fields_provided(repo: TaskRepository) -> None:
    task = _make_task(repo)
    _set_goal(
        repo,
        task.id,
        intent="Turn on the hallway light at 18:00",
        success_criteria="Hallway light is on",
        acceptance_test="Call get_device_state and confirm onoff=true",
    )
    updated = repo.get_task(task.id)
    assert updated is not None
    ctx = json.loads(updated.context or "{}")
    goal = ctx.get("goal", {})
    assert goal["intent"] == "Turn on the hallway light at 18:00"
    assert goal["success_criteria"] == "Hallway light is on"
    assert goal["acceptance_test"] == "Call get_device_state and confirm onoff=true"
    assert goal["outcome"] is None
    assert goal["completion_rejection_count"] == 0


def test_no_goal_when_fields_omitted(repo: TaskRepository) -> None:
    task = _make_task(repo)
    updated = repo.get_task(task.id)
    assert updated is not None
    ctx = json.loads(updated.context or "{}")
    assert "goal" not in ctx


# ---------------------------------------------------------------------------
# 2. complete_task — backward compat (no goal)
# ---------------------------------------------------------------------------


def test_complete_task_no_goal_completes_normally(repo: TaskRepository) -> None:
    task = _make_task(repo)
    assert task.status == "ACTIVE"
    completed = repo.transition_status(task.id, "COMPLETED")
    assert completed.status == "COMPLETED"


# ---------------------------------------------------------------------------
# 3. complete_task — goal present, goal_met=None (missing assessment)
# ---------------------------------------------------------------------------


def test_complete_task_with_goal_missing_assessment_detected(repo: TaskRepository) -> None:
    """Service layer: verify goal block is present and outcome is None (unassessed)."""
    task = _make_task(repo)
    _set_goal(repo, task.id, success_criteria="Device is on")
    updated = repo.get_task(task.id)
    assert updated is not None
    ctx = json.loads(updated.context or "{}")
    goal = ctx.get("goal", {})
    # outcome should still be None — no assessment yet
    assert goal["outcome"] is None
    # status should still be ACTIVE — no completion attempted
    assert updated.status == "ACTIVE"


# ---------------------------------------------------------------------------
# 4. complete_task — goal_met=False: rejection count incremented
# ---------------------------------------------------------------------------


def test_completion_rejection_count_increments(repo: TaskRepository) -> None:
    task = _make_task(repo)
    _set_goal(repo, task.id, success_criteria="Device is on")

    # Simulate rejection: increment count manually (mirrors tool logic)
    task2 = repo.get_task(task.id)
    assert task2 is not None
    ctx = json.loads(task2.context or "{}")
    goal = ctx["goal"]
    goal["completion_rejection_count"] = int(goal.get("completion_rejection_count", 0)) + 1
    ctx["goal"] = goal
    repo.update_task(task.id, context=json.dumps(ctx))

    updated = repo.get_task(task.id)
    assert updated is not None
    ctx2 = json.loads(updated.context or "{}")
    assert ctx2["goal"]["completion_rejection_count"] == 1
    assert updated.status == "ACTIVE"  # not completed


def test_completion_rejection_count_accumulates(repo: TaskRepository) -> None:
    task = _make_task(repo)
    _set_goal(repo, task.id, success_criteria="Device is on")

    for expected in range(1, 4):
        t = repo.get_task(task.id)
        assert t is not None
        ctx = json.loads(t.context or "{}")
        ctx["goal"]["completion_rejection_count"] = (
            int(ctx["goal"].get("completion_rejection_count", 0)) + 1
        )
        repo.update_task(task.id, context=json.dumps(ctx))

    final = repo.get_task(task.id)
    assert final is not None
    ctx_final = json.loads(final.context or "{}")
    assert ctx_final["goal"]["completion_rejection_count"] == 3


# ---------------------------------------------------------------------------
# 5. complete_task — goal_met=True, outcome stored
# ---------------------------------------------------------------------------


def test_outcome_stored_on_successful_completion(repo: TaskRepository) -> None:
    task = _make_task(repo)
    _set_goal(repo, task.id, success_criteria="Device is on")

    # Simulate successful completion: write outcome, then transition
    now = datetime.now(timezone.utc)
    t = repo.get_task(task.id)
    assert t is not None
    ctx = json.loads(t.context or "{}")
    ctx["goal"]["outcome"] = {
        "goal_met": True,
        "completion_basis": "criteria_met",
        "note": "Hallway light confirmed on via device state check.",
        "checked_at": now.isoformat(),
        "run_id": "test-run",
    }
    repo.update_task(task.id, context=json.dumps(ctx))
    repo.transition_status(task.id, "COMPLETED")

    completed = repo.get_task(task.id)
    assert completed is not None
    assert completed.status == "COMPLETED"
    ctx2 = json.loads(completed.context or "{}")
    outcome = ctx2["goal"]["outcome"]
    assert outcome["goal_met"] is True
    assert outcome["completion_basis"] == "criteria_met"
    assert "Hallway light" in outcome["note"]


# ---------------------------------------------------------------------------
# 6. schedule_task_followup — goal_missing emitted when no success_criteria
# ---------------------------------------------------------------------------


def test_goal_missing_detected_when_no_success_criteria(repo: TaskRepository) -> None:
    """Verify the task has no success_criteria (tool will emit task.goal_missing)."""
    task = _make_task(repo)
    # No goal set at all
    updated = repo.get_task(task.id)
    assert updated is not None
    ctx = json.loads(updated.context or "{}")
    goal = ctx.get("goal", {})
    assert not goal.get("success_criteria")


def test_goal_missing_not_emitted_when_criteria_present(repo: TaskRepository) -> None:
    """Verify success_criteria is present (tool should NOT emit goal_missing)."""
    task = _make_task(repo)
    _set_goal(repo, task.id, success_criteria="Light is on")
    updated = repo.get_task(task.id)
    assert updated is not None
    ctx = json.loads(updated.context or "{}")
    assert ctx["goal"]["success_criteria"] == "Light is on"


# ---------------------------------------------------------------------------
# 7. _render_full_task — goal fields rendered in active task prompt
# ---------------------------------------------------------------------------


def test_render_full_task_includes_goal_fields(repo: TaskRepository) -> None:
    task = _make_task(repo)
    _set_goal(
        repo,
        task.id,
        intent="Check if the front door is locked",
        success_criteria="Front door lock state is locked=true",
        acceptance_test="Call get_device_state on door lock, confirm locked=true",
    )
    output = _render_full_task(repo, repo.get_task(task.id))
    assert "original intent:" in output
    assert "Check if the front door is locked" in output
    assert "success criteria:" in output
    assert "locked=true" in output
    assert "acceptance test:" in output


def test_render_full_task_no_goal_no_goal_lines(repo: TaskRepository) -> None:
    task = _make_task(repo)
    output = _render_full_task(repo, repo.get_task(task.id))
    assert "original intent:" not in output
    assert "success criteria:" not in output
    assert "acceptance test:" not in output


def test_render_full_task_shows_outcome(repo: TaskRepository) -> None:
    task = _make_task(repo)
    _set_goal(repo, task.id, success_criteria="Light is on")
    t = repo.get_task(task.id)
    assert t is not None
    ctx = json.loads(t.context or "{}")
    ctx["goal"]["outcome"] = {
        "goal_met": True,
        "completion_basis": "criteria_met",
        "note": "Confirmed light is on.",
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "run_id": "test",
    }
    repo.update_task(task.id, context=json.dumps(ctx))
    output = _render_full_task(repo, repo.get_task(task.id))
    assert "outcome:" in output
    assert "Confirmed light is on." in output


def test_render_full_task_shows_rejection_count(repo: TaskRepository) -> None:
    task = _make_task(repo)
    _set_goal(repo, task.id, success_criteria="Light is on", completion_rejection_count=2)
    output = _render_full_task(repo, repo.get_task(task.id))
    assert "completion rejected: 2 time(s)" in output


def test_render_full_task_no_rejection_line_when_zero(repo: TaskRepository) -> None:
    task = _make_task(repo)
    _set_goal(repo, task.id, success_criteria="Light is on", completion_rejection_count=0)
    output = _render_full_task(repo, repo.get_task(task.id))
    assert "completion rejected" not in output
