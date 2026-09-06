"""Tests for app/scheduler/cleanup.py's purge_old_tasks.

TaskStep.task_id and TaskLink.task_id are FOREIGN KEY references to
task.id, and SQLite has foreign_keys=ON in production (see app/db.py) —
deleting a Task before its TaskStep/TaskLink children raises
IntegrityError there. The shared in_memory_engine fixture does not turn
on FK enforcement by default (SQLite's own default is off), so these
tests turn it on explicitly to actually exercise that failure mode
instead of silently tolerating it like most of the test suite's DB
fixtures do.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text
from sqlmodel import Session, select

from app.models.tasks import Task, TaskLink, TaskStep
from app.models.users import Household, User
from app.scheduler.cleanup import purge_old_tasks


@pytest.fixture
def db(monkeypatch: pytest.MonkeyPatch, in_memory_engine: object) -> Session:
    with Session(in_memory_engine) as s:  # type: ignore[arg-type]
        s.exec(text("PRAGMA foreign_keys=ON"))  # type: ignore[call-overload]
        s.add(Household(id="hh1", name="Test Household"))
        s.add(User(id="u1", household_id="hh1", name="Test User"))
        s.commit()

    @contextmanager  # type: ignore[misc]
    def _session():
        with Session(in_memory_engine) as s:  # type: ignore[arg-type]
            yield s

    monkeypatch.setattr("app.db.users_session", _session)
    with Session(in_memory_engine) as s:  # type: ignore[arg-type]
        yield s


def _make_task(session: Session, *, status: str, age_days: int) -> Task:
    task = Task(
        household_id="hh1",
        user_id="u1",
        title="Test task",
        status=status,
        created_at=datetime.now(timezone.utc) - timedelta(days=age_days),
    )
    session.add(task)
    session.commit()
    session.refresh(task)
    return task


async def test_purge_deletes_old_task_and_its_steps_and_links(db: Session) -> None:
    # Regression: purge_old_tasks used to bulk-delete Task rows without
    # deleting their TaskStep/TaskLink children first, which raised
    # IntegrityError under FK enforcement and left the old task in place
    # (the exception is caught and logged, not re-raised).
    task = _make_task(db, status="COMPLETED", age_days=30)
    task_id = task.id
    db.add(TaskStep(task_id=task_id, step_index=0, title="Step 1"))
    db.add(TaskLink(task_id=task_id, entity_type="member", entity_id="m1"))
    db.commit()

    await purge_old_tasks()

    assert db.get(Task, task_id) is None
    assert db.exec(select(TaskStep).where(TaskStep.task_id == task_id)).all() == []
    assert db.exec(select(TaskLink).where(TaskLink.task_id == task_id)).all() == []


async def test_purge_leaves_active_tasks_and_their_steps_alone(db: Session) -> None:
    task = _make_task(db, status="ACTIVE", age_days=30)
    db.add(TaskStep(task_id=task.id, step_index=0, title="Step 1"))
    db.commit()

    await purge_old_tasks()

    assert db.get(Task, task.id) is not None
    assert db.exec(select(TaskStep).where(TaskStep.task_id == task.id)).all() != []


async def test_purge_leaves_recent_completed_tasks_alone(db: Session) -> None:
    task = _make_task(db, status="COMPLETED", age_days=1)

    await purge_old_tasks()

    assert db.get(Task, task.id) is not None
