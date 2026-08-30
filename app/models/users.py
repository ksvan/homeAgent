from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlmodel import Field, SQLModel, UniqueConstraint


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
    onboarding_complete: bool = Field(default=False)
    # Global kill switch, independent of the per-surface access flags planned
    # for Phase 2 (docs/household-identity-and-access-design.md Option D) —
    # authorize() denies everything for an inactive account regardless of
    # surface. Not exposed anywhere yet; defaults true so existing accounts
    # are unaffected.
    is_active: bool = Field(default=True)
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
    __table_args__ = (UniqueConstraint("channel", "channel_user_id"),)

    id: str = Field(default_factory=_uuid, primary_key=True)
    user_id: str = Field(foreign_key="user.id", index=True)
    channel: str
    channel_user_id: str
    created_at: datetime = Field(default_factory=_now)


class WebAuthnCredential(SQLModel, table=True):
    """A registered passkey for a User — see
    docs/household-identity-and-access-design.md Option F.

    Public key material only; the private key never leaves the
    authenticator, so nothing here is secret or needs encryption at rest
    (contrast IntegrationAccount's OAuth tokens). `credential_id` and
    `public_key` are the base64url-encoded values WebAuthn libraries
    hand back from the registration ceremony. `sign_count` backs the
    cloned-authenticator check: a counter that doesn't strictly increase
    on successive authentications is the standard signal of a cloned
    credential.
    """

    id: str = Field(default_factory=_uuid, primary_key=True)
    user_id: str = Field(foreign_key="user.id", index=True)
    credential_id: str = Field(unique=True, index=True)
    public_key: str
    sign_count: int = 0
    device_label: str = ""
    created_at: datetime = Field(default_factory=_now)
    last_used_at: datetime = Field(default_factory=_now)
