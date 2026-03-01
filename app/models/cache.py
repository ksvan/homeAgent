from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Field, SQLModel


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _uuid() -> str:
    return str(uuid.uuid4())


class DeviceSnapshot(SQLModel, table=True):
    id: str = Field(default_factory=_uuid, primary_key=True)
    household_id: str = Field(index=True)
    device_id: str = Field(index=True)
    capability: str
    # JSON-encoded value
    value: str
    updated_at: datetime = Field(default_factory=_now)
    # "homey_event" | "agent_action" | "poll" | "verify"
    source: str = "poll"


class EventLog(SQLModel, table=True):
    id: str = Field(default_factory=_uuid, primary_key=True)
    # "telegram_message" | "home_event" | "reminder_fired" | "agent_trigger"
    event_type: str
    household_id: str = Field(index=True)
    user_id: Optional[str] = Field(default=None, index=True)
    # JSON-encoded full event payload
    payload: str = "{}"
    created_at: datetime = Field(default_factory=_now)


class AgentRunLog(SQLModel, table=True):
    id: str = Field(default_factory=_uuid, primary_key=True)
    household_id: str = Field(index=True)
    user_id: str = Field(index=True)
    trigger_event_id: Optional[str] = Field(default=None, index=True)
    model_used: str
    input_summary: str = ""
    # JSON array: [{tool, args, result, verified}, ...]
    tools_called: str = "[]"
    output_summary: str = ""
    duration_ms: int = 0
    # JSON: {input: N, output: N}
    tokens_used: str = "{}"
    created_at: datetime = Field(default_factory=_now)


class PendingAction(SQLModel, table=True):
    # UUID token encoded in the Telegram callback_data
    token: str = Field(default_factory=_uuid, primary_key=True)
    household_id: str = Field(index=True)
    user_id: str = Field(index=True)
    tool_name: str
    # JSON-encoded tool arguments
    tool_args: str = "{}"
    policy_name: str
    created_at: datetime = Field(default_factory=_now)
    expires_at: datetime
