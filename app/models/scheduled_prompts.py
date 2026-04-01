from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Field, SQLModel


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _uuid() -> str:
    return str(uuid.uuid4())


class ScheduledPrompt(SQLModel, table=True):
    id: str = Field(default_factory=_uuid, primary_key=True)
    household_id: str = Field(index=True)
    user_id: str = Field(index=True)
    channel_user_id: str  # Telegram chat ID to deliver the response to
    name: str  # Human-readable label, e.g. "Weekly football summary"
    prompt: str  # The text passed to run_conversation when this fires
    recurrence: str  # "daily" | "weekly:sun" | "monthly:15" | "once"
    time_of_day: str  # "HH:MM" in 24h format (unused when recurrence="once")
    run_at: Optional[datetime] = Field(default=None, nullable=True)  # set when recurrence="once"
    enabled: bool = True
    created_at: datetime = Field(default_factory=_now)

    # --- Proactive behaviour metadata (V1) ---
    behavior_kind: Optional[str] = Field(default=None, nullable=True)
    goal: Optional[str] = Field(default=None, nullable=True)
    config_json: Optional[str] = Field(default=None, nullable=True)
    delivery_policy_json: Optional[str] = Field(default=None, nullable=True)

    # --- Last-run state ---
    last_fired_at: Optional[datetime] = Field(default=None, nullable=True)
    last_delivered_at: Optional[datetime] = Field(default=None, nullable=True)
    last_status: Optional[str] = Field(default=None, nullable=True)
    last_result_hash: Optional[str] = Field(default=None, nullable=True)
    last_result_preview: Optional[str] = Field(default=None, nullable=True)


class ScheduledPromptRun(SQLModel, table=True):
    """Audit/history row — one per scheduled prompt firing."""

    id: str = Field(default_factory=_uuid, primary_key=True)
    prompt_id: str = Field(index=True)
    fired_at: datetime
    finished_at: Optional[datetime] = None
    status: str  # "delivered" | "skipped" | "failed"
    skip_reason: Optional[str] = None
    run_id: Optional[str] = None  # agent run UUID if the agent was invoked
    output_hash: Optional[str] = None
    output_preview: Optional[str] = None
    created_at: datetime = Field(default_factory=_now)


class ScheduledPromptLink(SQLModel, table=True):
    """Links a scheduled prompt to a world-model entity."""

    id: str = Field(default_factory=_uuid, primary_key=True)
    prompt_id: str = Field(index=True)
    entity_type: str  # "member" | "calendar" | "device" | "place" | "routine" | "task"
    entity_id: str
    role: str = "subject"  # "subject" | "source" | "target" | "focus"
    created_at: datetime = Field(default_factory=_now)
