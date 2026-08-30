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
    # JSON: {input, output, cache_read, cache_write, static_prompt_cache_version}
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
    # Which MCP server owns this tool ("homey" | "oda") — execute_pending_action
    # dispatches on this rather than assuming Homey. Defaults to "homey" since
    # that was the only provider before Oda existed.
    provider: str = "homey"
    created_at: datetime = Field(default_factory=_now)
    expires_at: datetime


class OAuthState(SQLModel, table=True):
    """Short-lived, one-time PKCE/state row for an in-progress OAuth connect
    flow (see docs/oda-grocery-mcp-tool-design.md "New setting" / "Flow").

    The callback handler must read + delete this row atomically, verify
    `state` (the primary key) matches, and reuse `redirect_uri` exactly —
    OAuth token exchange requires an identical redirect_uri to the one used
    in the authorize request.

    `client_id`/`client_secret` come from dynamic client registration, which
    happens once per connect attempt (see app.oda.oauth.register_client) —
    stashed here so the callback can complete the token exchange with the
    same registered client. `client_secret` is encrypted at rest like
    IntegrationAccount's token fields, even though this row is short-lived.
    """

    state: str = Field(primary_key=True)
    provider: str
    household_id: str = Field(index=True)
    initiating_user_id: str
    pkce_verifier: str
    client_id: str = ""
    client_secret: str = ""  # Fernet-encrypted; empty for public (no-secret) clients
    redirect_uri: str
    created_at: datetime = Field(default_factory=_now)
    expires_at: datetime


class WebChatSession(SQLModel, table=True):
    """A web chat login session — see
    docs/household-identity-and-access-design.md Option D / Goal 7.

    Only `token_hash` (SHA-256 hex digest) is ever stored — the raw bearer
    token exists solely in memory at issuance time and in the caller's
    hands afterward, never persisted, so a stolen DB backup can't be
    replayed as a live session. `expires_at` slides forward on each use
    (`touch_session`); `absolute_expires_at` is fixed at creation and never
    extended, capping how long a session can live regardless of activity.
    `revoked_at` marks an explicit logout/admin-revoke — kept as a row
    (not deleted) so it remains visible for audit; a session that merely
    expired is deleted outright since natural expiry isn't an audit-worthy
    event the way an explicit revoke is.
    """

    id: str = Field(default_factory=_uuid, primary_key=True)
    token_hash: str = Field(unique=True, index=True)
    user_id: str = Field(index=True)
    household_id: str = Field(index=True)
    created_at: datetime = Field(default_factory=_now)
    last_seen_at: datetime = Field(default_factory=_now)
    expires_at: datetime
    absolute_expires_at: datetime
    revoked_at: Optional[datetime] = Field(default=None)
