from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Field, SQLModel


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _uuid() -> str:
    return str(uuid.uuid4())


# Valid task statuses
TERMINAL_STATUSES = {"COMPLETED", "FAILED", "CANCELLED"}

# Allowed status transitions
ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "ACTIVE": {
        "AWAITING_INPUT", "AWAITING_CONFIRMATION", "AWAITING_RESUME",
        "COMPLETED", "FAILED", "CANCELLED",
    },
    "AWAITING_RESUME": {"ACTIVE", "FAILED", "CANCELLED"},
    "AWAITING_INPUT": {"ACTIVE", "CANCELLED"},
    "AWAITING_CONFIRMATION": {"ACTIVE", "CANCELLED"},
}


class Task(SQLModel, table=True):
    id: str = Field(default_factory=_uuid, primary_key=True)
    household_id: str = Field(index=True)
    user_id: str = Field(foreign_key="user.id", index=True)
    title: str
    # ACTIVE | AWAITING_INPUT | AWAITING_CONFIRMATION | AWAITING_RESUME
    # COMPLETED | FAILED | CANCELLED
    status: str = "ACTIVE"
    # "plan" | "track" | "prepare" | "handoff" | "legacy"
    task_kind: Optional[str] = None
    # JSON array: [{description, status, completed_at}, ...]  (legacy — prefer TaskStep)
    steps: str = "[]"
    current_step: int = 0
    # Compact human-readable progress summary for context injection
    summary: Optional[str] = None
    # What the user must answer when status=AWAITING_INPUT
    awaiting_input_hint: Optional[str] = None
    # Time-based wakeup — nullable datetime
    resume_after: Optional[datetime] = None
    # Link to the agent run that last touched this task
    last_agent_run_id: Optional[str] = None
    # JSON — task-specific state (e.g. gathered options, intermediate results)
    context: str = "{}"
    # FK to eventlog.id — what triggered this task
    trigger_event_id: Optional[str] = None
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)
    completed_at: Optional[datetime] = None


class TaskLink(SQLModel, table=True):
    id: str = Field(default_factory=_uuid, primary_key=True)
    task_id: str = Field(foreign_key="task.id", index=True)
    # "member" | "calendar" | "place" | "device" | "routine" | "scheduled_prompt"
    entity_type: str
    entity_id: str
    # "subject" | "source" | "target" | "selected_option"
    role: str = "subject"
    created_at: datetime = Field(default_factory=_now)


class TaskStep(SQLModel, table=True):
    id: str = Field(default_factory=_uuid, primary_key=True)
    task_id: str = Field(foreign_key="task.id", index=True)
    step_index: int
    title: str
    # "pending" | "active" | "done" | "failed" | "cancelled"
    status: str = "pending"
    # "research" | "decision" | "tool" | "wait" | "message"
    step_type: str = "research"
    # JSON — step-specific working state
    details_json: str = "{}"
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    updated_at: datetime = Field(default_factory=_now)
