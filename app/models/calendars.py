from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Field, SQLModel


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _uuid() -> str:
    return str(uuid.uuid4())


class Calendar(SQLModel, table=True):
    id: str = Field(default_factory=_uuid, primary_key=True)
    household_id: str = Field(index=True)
    name: str
    url: str
    member_name: Optional[str] = None
    category: str = "general"
    created_at: datetime = Field(default_factory=_now)
