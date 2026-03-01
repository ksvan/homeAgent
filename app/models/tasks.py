from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Field, SQLModel


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _uuid() -> str:
    return str(uuid.uuid4())


class Task(SQLModel, table=True):
    id: str = Field(default_factory=_uuid, primary_key=True)
    household_id: str = Field(index=True)
    user_id: str = Field(foreign_key="user.id", index=True)
    title: str
    # ACTIVE | AWAITING_INPUT | AWAITING_CONFIRMATION | COMPLETED | FAILED | CANCELLED
    status: str = "ACTIVE"
    # JSON array: [{description, status, completed_at}, ...]
    steps: str = "[]"
    current_step: int = 0
    # JSON — task-specific state (e.g. gathered options, intermediate results)
    context: str = "{}"
    # FK to eventlog.id — what triggered this task
    trigger_event_id: Optional[str] = None
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)
    completed_at: Optional[datetime] = None
