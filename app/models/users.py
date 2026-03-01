from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlmodel import Field, SQLModel


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _uuid() -> str:
    return str(uuid.uuid4())


class Household(SQLModel, table=True):
    id: str = Field(default_factory=_uuid, primary_key=True)
    name: str
    timezone: str = "UTC"
    created_at: datetime = Field(default_factory=_now)


class User(SQLModel, table=True):
    id: str = Field(default_factory=_uuid, primary_key=True)
    household_id: str = Field(foreign_key="household.id", index=True)
    telegram_id: int = Field(unique=True, index=True)
    name: str
    is_admin: bool = False
    preferred_channel: str = "telegram"
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class ActionPolicy(SQLModel, table=True):
    id: str = Field(default_factory=_uuid, primary_key=True)
    # Human-readable name, e.g. "Door lock/unlock"
    name: str = Field(unique=True, index=True)
    # fnmatch glob matched against the Homey tool name (prefix stripped)
    tool_pattern: str
    # JSON dict: arg-name → fnmatch pattern applied to string values
    arg_conditions: str = "{}"
    # "low" | "medium" | "high"
    impact_level: str = "medium"
    requires_confirm: bool = False
    # Message shown to the user in the Telegram confirmation prompt
    confirm_message: str = ""
    cooldown_seconds: int = 0
    enabled: bool = True
    created_at: datetime = Field(default_factory=_now)


class ChannelMapping(SQLModel, table=True):
    id: str = Field(default_factory=_uuid, primary_key=True)
    user_id: str = Field(foreign_key="user.id", index=True)
    channel: str
    channel_user_id: str
    created_at: datetime = Field(default_factory=_now)
