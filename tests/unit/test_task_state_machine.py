"""Unit tests for the task status state machine and step progression.

Tests TaskRepository.transition_status(), advance_step(), create_task(),
and get_active_tasks() using an in-memory SQLite database.

The `users_session` context manager in app.tasks.repository is monkeypatched
to use the per-test in-memory engine so no real DB is touched.
"""
from __future__ import annotations

from contextlib import contextmanager

import pytest
from sqlmodel import Session

from app.models.tasks import ALLOWED_TRANSITIONS, TERMINAL_STATUSES, Task, TaskStep
from app.tasks.repository import TaskRepository


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def repo(monkeypatch: pytest.MonkeyPatch, in_memory_engine: object) -> TaskRepository:
    """TaskRepository wired to the in-memory DB."""

    @contextmanager
    def _session():  # type: ignore[misc]
        with Session(in_memory_engine) as s:  # type: ignore[arg-type]
            yield s

    monkeypatch.setattr("app.tasks.repository.users_session", _session)
    return TaskRepository()


def _make_task(repo: TaskRepository, status: str = "ACTIVE", steps: int = 0) -> Task:
    step_defs = [{"title": f"Step {i}", "step_type": "research"} for i in range(steps)]
    task = repo.create_task(
        household_id="hh-1",
        user_id="user-1",
        title="Test task",
        steps=step_defs or None,
    )
    if status != "ACTIVE":
        task = repo.transition_status(task.id, status)
    return task


# ---------------------------------------------------------------------------
# create_task
# ---------------------------------------------------------------------------


def test_create_task_default_status_is_active(repo: TaskRepository) -> None:
    task = repo.create_task(household_id="hh-1", user_id="u-1", title="My task")
    assert task.status == "ACTIVE"
    assert task.id


def test_create_task_with_steps_first_step_is_active(repo: TaskRepository) -> None:
    task = repo.create_task(
        household_id="hh-1",
        user_id="u-1",
        title="Stepped task",
        steps=[
            {"title": "Alpha", "step_type": "research"},
            {"title": "Beta", "step_type": "tool"},
        ],
    )
    steps = repo.get_steps(task.id)
    assert len(steps) == 2
    assert steps[0].status == "active"
    assert steps[1].status == "pending"


def test_create_task_without_steps_has_no_steps(repo: TaskRepository) -> None:
    task = repo.create_task(household_id="hh-1", user_id="u-1", title="No steps")
    assert repo.get_steps(task.id) == []


# ---------------------------------------------------------------------------
# transition_status — valid paths
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "from_status,to_status",
    [
        ("ACTIVE", "AWAITING_INPUT"),
        ("ACTIVE", "AWAITING_CONFIRMATION"),
        ("ACTIVE", "AWAITING_RESUME"),
        ("ACTIVE", "COMPLETED"),
        ("ACTIVE", "FAILED"),
        ("ACTIVE", "CANCELLED"),
        ("AWAITING_RESUME", "ACTIVE"),
        ("AWAITING_RESUME", "FAILED"),
        ("AWAITING_RESUME", "CANCELLED"),
        ("AWAITING_INPUT", "ACTIVE"),
        ("AWAITING_INPUT", "CANCELLED"),
        ("AWAITING_CONFIRMATION", "ACTIVE"),
        ("AWAITING_CONFIRMATION", "CANCELLED"),
    ],
)
def test_valid_transitions_succeed(
    repo: TaskRepository, from_status: str, to_status: str
) -> None:
    task = _make_task(repo, status=from_status)
    updated = repo.transition_status(task.id, to_status)
    assert updated.status == to_status


# ---------------------------------------------------------------------------
# transition_status — invalid paths
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "from_status,to_status",
    [
        ("ACTIVE", "ACTIVE"),          # self-transition
        ("COMPLETED", "ACTIVE"),       # terminal → anything
        ("FAILED", "ACTIVE"),
        ("CANCELLED", "ACTIVE"),
        ("AWAITING_INPUT", "COMPLETED"),
        ("AWAITING_CONFIRMATION", "FAILED"),
    ],
)
def test_invalid_transitions_raise_value_error(
    repo: TaskRepository, from_status: str, to_status: str
) -> None:
    task = _make_task(repo, status=from_status)
    with pytest.raises(ValueError):
        repo.transition_status(task.id, to_status)


def test_all_allowed_transitions_are_tested() -> None:
    """Ensure our parametrized list covers every defined allowed transition."""
    covered = {
        ("ACTIVE", "AWAITING_INPUT"),
        ("ACTIVE", "AWAITING_CONFIRMATION"),
        ("ACTIVE", "AWAITING_RESUME"),
        ("ACTIVE", "COMPLETED"),
        ("ACTIVE", "FAILED"),
        ("ACTIVE", "CANCELLED"),
        ("AWAITING_RESUME", "ACTIVE"),
        ("AWAITING_RESUME", "FAILED"),
        ("AWAITING_RESUME", "CANCELLED"),
        ("AWAITING_INPUT", "ACTIVE"),
        ("AWAITING_INPUT", "CANCELLED"),
        ("AWAITING_CONFIRMATION", "ACTIVE"),
        ("AWAITING_CONFIRMATION", "CANCELLED"),
    }
    defined = {
        (from_s, to_s)
        for from_s, to_set in ALLOWED_TRANSITIONS.items()
        for to_s in to_set
    }
    assert covered == defined, f"Untested transitions: {defined - covered}"


# ---------------------------------------------------------------------------
# Terminal status sets completed_at
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("terminal", TERMINAL_STATUSES)
def test_terminal_transition_sets_completed_at(
    repo: TaskRepository, terminal: str
) -> None:
    task = _make_task(repo)
    updated = repo.transition_status(task.id, terminal)
    assert updated.completed_at is not None


def test_non_terminal_transition_leaves_completed_at_none(repo: TaskRepository) -> None:
    task = _make_task(repo)
    updated = repo.transition_status(task.id, "AWAITING_INPUT")
    assert updated.completed_at is None


# ---------------------------------------------------------------------------
# get_active_tasks — excludes terminal-status tasks
# ---------------------------------------------------------------------------


def test_get_active_tasks_excludes_terminal(repo: TaskRepository) -> None:
    active = _make_task(repo, status="ACTIVE")
    awaiting = _make_task(repo, status="AWAITING_INPUT")
    _make_task(repo, status="COMPLETED")
    _make_task(repo, status="FAILED")
    _make_task(repo, status="CANCELLED")

    result_ids = {t.id for t in repo.get_active_tasks("user-1")}
    assert active.id in result_ids
    assert awaiting.id in result_ids
    assert len(result_ids) == 2


def test_get_active_tasks_empty_when_none(repo: TaskRepository) -> None:
    assert repo.get_active_tasks("user-unknown") == []


# ---------------------------------------------------------------------------
# advance_step
# ---------------------------------------------------------------------------


def test_advance_step_marks_current_done_and_activates_next(repo: TaskRepository) -> None:
    task = repo.create_task(
        household_id="hh-1",
        user_id="u-1",
        title="Steps",
        steps=[
            {"title": "A", "step_type": "research"},
            {"title": "B", "step_type": "tool"},
            {"title": "C", "step_type": "tool"},
        ],
    )
    repo.advance_step(task.id, completed_step_index=0)
    steps = repo.get_steps(task.id)
    by_idx = {s.step_index: s for s in steps}

    assert by_idx[0].status == "done"
    assert by_idx[0].completed_at is not None
    assert by_idx[1].status == "active"
    assert by_idx[1].started_at is not None
    assert by_idx[2].status == "pending"


def test_advance_step_on_last_step_leaves_no_next_active(repo: TaskRepository) -> None:
    task = repo.create_task(
        household_id="hh-1",
        user_id="u-1",
        title="Two steps",
        steps=[
            {"title": "A", "step_type": "research"},
            {"title": "B", "step_type": "tool"},
        ],
    )
    # Advance past step 0 first
    repo.advance_step(task.id, completed_step_index=0)
    # Now advance last step
    repo.advance_step(task.id, completed_step_index=1)

    steps = repo.get_steps(task.id)
    assert all(s.status == "done" for s in steps)
