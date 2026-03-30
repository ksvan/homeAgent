"""Task repository — CRUD for Task and TaskStep."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from sqlmodel import select

from app.db import users_session
from app.models.tasks import ALLOWED_TRANSITIONS, TERMINAL_STATUSES, Task, TaskLink, TaskStep

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


class TaskRepository:
    # ------------------------------------------------------------------
    # Task CRUD
    # ------------------------------------------------------------------

    def create_task(
        self,
        household_id: str,
        user_id: str,
        title: str,
        task_kind: str = "plan",
        summary: str | None = None,
        steps: list[dict] | None = None,
        context: dict | None = None,
    ) -> Task:
        """Create a task with optional initial steps in a single transaction."""
        with users_session() as session:
            task = Task(
                household_id=household_id,
                user_id=user_id,
                title=title,
                task_kind=task_kind,
                summary=summary,
                context=json.dumps(context or {}),
            )
            session.add(task)
            session.flush()

            if steps:
                for idx, step_def in enumerate(steps):
                    step = TaskStep(
                        task_id=task.id,
                        step_index=idx,
                        title=step_def.get("title", f"Step {idx + 1}"),
                        step_type=step_def.get("step_type", "research"),
                        status="active" if idx == 0 else "pending",
                        started_at=_now() if idx == 0 else None,
                    )
                    session.add(step)

            session.commit()
            session.refresh(task)
            return task

    def get_task(self, task_id: str) -> Task | None:
        with users_session() as session:
            return session.get(Task, task_id)

    def get_active_tasks(self, user_id: str) -> list[Task]:
        """All non-terminal tasks for a user, ordered by most recent first."""
        with users_session() as session:
            return list(
                session.exec(
                    select(Task)
                    .where(Task.user_id == user_id, Task.status.notin_(TERMINAL_STATUSES))  # type: ignore[attr-defined]
                    .order_by(Task.updated_at.desc())  # type: ignore[union-attr]
                ).all()
            )

    def get_active_tasks_for_household(self, household_id: str) -> list[Task]:
        with users_session() as session:
            return list(
                session.exec(
                    select(Task)
                    .where(Task.household_id == household_id, Task.status.notin_(TERMINAL_STATUSES))  # type: ignore[attr-defined]
                    .order_by(Task.updated_at.desc())  # type: ignore[union-attr]
                ).all()
            )

    def update_task(self, task_id: str, **fields: object) -> Task:
        """Generic field update with automatic updated_at bump."""
        with users_session() as session:
            task = session.get(Task, task_id)
            if task is None:
                raise ValueError(f"Task {task_id} not found")

            for key, value in fields.items():
                if not hasattr(task, key):
                    raise ValueError(f"Task has no field {key!r}")
                setattr(task, key, value)

            task.updated_at = _now()
            session.add(task)
            session.commit()
            session.refresh(task)
            return task

    def transition_status(self, task_id: str, new_status: str) -> Task:
        """Validate and apply a status transition."""
        with users_session() as session:
            task = session.get(Task, task_id)
            if task is None:
                raise ValueError(f"Task {task_id} not found")

            allowed = ALLOWED_TRANSITIONS.get(task.status, set())
            if new_status not in allowed:
                raise ValueError(
                    f"Cannot transition from {task.status} to {new_status}. "
                    f"Allowed: {sorted(allowed) if allowed else 'none (terminal state)'}"
                )

            task.status = new_status
            task.updated_at = _now()

            if new_status in TERMINAL_STATUSES:
                task.completed_at = _now()

            session.add(task)
            session.commit()
            session.refresh(task)
            return task

    # ------------------------------------------------------------------
    # TaskStep CRUD
    # ------------------------------------------------------------------

    def get_steps(self, task_id: str) -> list[TaskStep]:
        """Steps for a task, ordered by step_index."""
        with users_session() as session:
            return list(
                session.exec(
                    select(TaskStep)
                    .where(TaskStep.task_id == task_id)
                    .order_by(TaskStep.step_index)
                ).all()
            )

    def update_step(self, step_id: str, **fields: object) -> TaskStep:
        with users_session() as session:
            step = session.get(TaskStep, step_id)
            if step is None:
                raise ValueError(f"TaskStep {step_id} not found")

            for key, value in fields.items():
                if not hasattr(step, key):
                    raise ValueError(f"TaskStep has no field {key!r}")
                setattr(step, key, value)

            step.updated_at = _now()
            session.add(step)
            session.commit()
            session.refresh(step)
            return step

    def advance_step(self, task_id: str, completed_step_index: int) -> Task:
        """Mark the given step done and activate the next one."""
        now = _now()
        with users_session() as session:
            steps = list(
                session.exec(
                    select(TaskStep)
                    .where(TaskStep.task_id == task_id)
                    .order_by(TaskStep.step_index)
                ).all()
            )

            for step in steps:
                if step.step_index == completed_step_index:
                    step.status = "done"
                    step.completed_at = now
                    step.updated_at = now
                    session.add(step)
                elif step.step_index == completed_step_index + 1:
                    step.status = "active"
                    step.started_at = now
                    step.updated_at = now
                    session.add(step)

            task = session.get(Task, task_id)
            if task is None:
                raise ValueError(f"Task {task_id} not found")

            next_index = completed_step_index + 1
            task.current_step = next_index
            task.updated_at = now
            session.add(task)

            session.commit()
            session.refresh(task)
            return task

    # ------------------------------------------------------------------
    # TaskLink CRUD
    # ------------------------------------------------------------------

    def add_link(
        self,
        task_id: str,
        entity_type: str,
        entity_id: str,
        role: str = "subject",
    ) -> TaskLink:
        with users_session() as session:
            link = TaskLink(
                task_id=task_id,
                entity_type=entity_type,
                entity_id=entity_id,
                role=role,
            )
            session.add(link)
            session.commit()
            session.refresh(link)
            return link

    def get_links(self, task_id: str) -> list[TaskLink]:
        with users_session() as session:
            return list(
                session.exec(
                    select(TaskLink).where(TaskLink.task_id == task_id)
                ).all()
            )

    def remove_link(self, link_id: str) -> bool:
        with users_session() as session:
            link = session.get(TaskLink, link_id)
            if link is None:
                return False
            session.delete(link)
            session.commit()
            return True
