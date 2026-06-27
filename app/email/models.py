from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Field, SQLModel


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class EmailMessage(SQLModel, table=True):
    """Durable intake row for one inbound AgentMail message."""

    id: str = Field(default_factory=_uuid, primary_key=True)
    provider: str = "agentmail"

    # Provider-level deduplication keys
    provider_event_id: Optional[str] = Field(default=None, index=True)
    provider_delivery_id: Optional[str] = Field(default=None, index=True)  # Svix svix-id
    provider_message_id: str = Field(index=True)  # RFC 2822 Message-ID
    provider_thread_id: Optional[str] = Field(default=None)
    provider_inbox_id: str

    # User resolution
    household_id: Optional[str] = Field(default=None, index=True)
    user_id: Optional[str] = Field(default=None, index=True)
    channel_user_id: str  # normalized sender email

    # Envelope
    from_email: str
    to_json: str = "[]"
    cc_json: str = "[]"
    subject: str = ""
    timestamp: Optional[datetime] = None

    # Processing state machine
    # RECEIVED | CLASSIFYING | NEEDS_CONFIRMATION | CONFIRMED | PROCESSING
    # | PROCESSED | IGNORED | RATE_LIMITED | FAILED_RETRYABLE | DEAD_LETTER
    status: str = Field(default="RECEIVED", index=True)
    status_reason: Optional[str] = None
    attempt_count: int = 0
    next_attempt_at: Optional[datetime] = Field(default=None, index=True)
    locked_at: Optional[datetime] = None
    last_error: Optional[str] = None

    # Authentication (parsed from Authentication-Results header)
    # "pass" | "fail" | "unknown"
    auth_status: Optional[str] = None
    auth_details_json: Optional[str] = None

    reply_to_email: Optional[str] = None

    # Derived content
    instruction_text: str = ""
    intake_summary_text: str = ""
    proposed_action_json: Optional[str] = None
    confirmation_id: Optional[str] = None  # -> EmailIntakeConfirmation.token
    confirmed_at: Optional[datetime] = None

    # Storage
    provider_metadata_json: str = "{}"
    raw_debug_json: Optional[str] = None
    raw_debug_expires_at: Optional[datetime] = None

    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)
    processed_at: Optional[datetime] = None


class EmailAttachment(SQLModel, table=True):
    """Metadata-only attachment record (bodies not fetched in V1)."""

    id: str = Field(default_factory=_uuid, primary_key=True)
    email_message_id: str = Field(index=True)
    provider_attachment_id: str
    filename: str = ""
    content_type: str = ""
    size: int = 0
    inline: bool = False


class EmailIntakeConfirmation(SQLModel, table=True):
    """Pending Telegram Yes/No confirmation for an email intake action."""

    token: str = Field(default_factory=_uuid, primary_key=True)
    email_message_id: str = Field(index=True)
    user_id: str
    household_id: str
    intake_text: str  # pre-built text passed to agent_run on confirm
    expires_at: datetime
    created_at: datetime = Field(default_factory=_now)
