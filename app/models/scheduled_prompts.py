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
    recurrence: str  # "daily" | "weekly:sun" | "monthly:15"
    time_of_day: str  # "HH:MM" in 24h format
    enabled: bool = True
    created_at: datetime = Field(default_factory=_now)
