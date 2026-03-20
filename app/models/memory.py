from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Field, SQLModel


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _uuid() -> str:
    return str(uuid.uuid4())


class UserProfile(SQLModel, table=True):
    id: str = Field(default_factory=_uuid, primary_key=True)
    # user_id references User.id in users.db — no SQLite FK across DB files
    user_id: str = Field(unique=True, index=True)
    # JSON-encoded dict of profile facts
    summary: str = "{}"
    updated_at: datetime = Field(default_factory=_now)


class HouseholdProfile(SQLModel, table=True):
    id: str = Field(default_factory=_uuid, primary_key=True)
    # household_id references Household.id in users.db — no SQLite FK across DB files
    household_id: str = Field(unique=True, index=True)
    # JSON-encoded dict of household facts
    summary: str = "{}"
    updated_at: datetime = Field(default_factory=_now)


class EpisodicMemory(SQLModel, table=True):
    id: str = Field(default_factory=_uuid, primary_key=True)
    household_id: str = Field(index=True)
    user_id: Optional[str] = Field(default=None, index=True)
    content: str
    # Rowid in the sqlite-vec virtual table (None if embedding failed)
    embedding_id: Optional[str] = None
    created_at: datetime = Field(default_factory=_now)
    # FK to agentrunlog.id — which run produced this memory
    source_run_id: Optional[str] = None
    # Lifecycle fields
    importance: str = Field(default="normal")  # critical | important | normal | ephemeral
    last_used_at: Optional[datetime] = None    # updated each time the memory is retrieved


class ConversationMessage(SQLModel, table=True):
    id: str = Field(default_factory=_uuid, primary_key=True)
    # user_id references User.id in users.db — no SQLite FK across DB files
    user_id: str = Field(index=True)
    # "user" | "assistant"
    role: str
    content: str
    created_at: datetime = Field(default_factory=_now)


class ConversationTurn(SQLModel, table=True):
    """Full pydantic-ai message list for one conversation turn (user + tool calls + assistant)."""

    id: str = Field(default_factory=_uuid, primary_key=True)
    user_id: str = Field(index=True)
    # JSON-serialized list[ModelMessage] from result.new_messages()
    messages_json: str
    created_at: datetime = Field(default_factory=_now)


class ConversationSummary(SQLModel, table=True):
    id: str = Field(default_factory=_uuid, primary_key=True)
    # user_id references User.id in users.db — no SQLite FK across DB files
    user_id: str = Field(unique=True, index=True)
    summary: str
    # The ConversationMessage.id up to which this summary covers
    covers_through_message_id: str
    created_at: datetime = Field(default_factory=_now)
