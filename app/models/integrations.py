"""Household-scoped OAuth/settings-based tool integrations.

Generic across providers by design (see docs/oda-grocery-mcp-tool-design.md
"New: admin Integrations page") — Oda is the first provider, not the only
one this table is meant to support. Lives in users.db alongside other
durable household configuration.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlmodel import Field, SQLModel, UniqueConstraint


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _uuid() -> str:
    return str(uuid.uuid4())


class IntegrationAccount(SQLModel, table=True):
    """One connected OAuth account for a household + provider pair.

    Token fields are encrypted at rest (see app.integrations.crypto) — never
    store or log plaintext tokens.
    """

    __table_args__ = (UniqueConstraint("household_id", "provider"),)

    id: str = Field(default_factory=_uuid, primary_key=True)
    household_id: str = Field(foreign_key="household.id", index=True)
    provider: str  # "oda"

    access_token: str  # Fernet-encrypted
    refresh_token: str  # Fernet-encrypted
    expires_at: datetime

    # Dynamic client registration (RFC 7591) result for this provider.
    client_id: str = ""
    client_secret: str = ""  # Fernet-encrypted; empty if DCR used "none" auth

    connected_by_user_id: str = Field(foreign_key="user.id")
    connected_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)
