"""
Thin wrapper around the AgentMail Python SDK.

All SDK interaction is funnelled through this module so the rest of the
email package never imports `agentmail` directly.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from email.utils import parseaddr
from typing import Any, Optional

logger = logging.getLogger(__name__)


@dataclass
class AgentMailMessage:
    """Normalised representation of an AgentMail message."""

    inbox_id: str
    message_id: str
    thread_id: Optional[str]
    from_email: str  # normalized bare email address
    from_display: str  # full "Name <email>" string from API
    to: list[str]
    cc: list[str]
    subject: str
    text: Optional[str]
    html: Optional[str]
    timestamp: Optional[datetime]
    in_reply_to: Optional[str]
    references: list[str]
    attachments: list[dict[str, Any]]
    headers: dict[str, str]
    size: int
    labels: list[str]
    created_at: Optional[datetime]
    raw: dict[str, Any] = field(default_factory=dict)


def _norm_email(addr: str) -> str:
    """Extract and lowercase the bare email address from a display-name string."""
    _, email = parseaddr(addr)
    return email.strip().lower()


def get_client() -> Any:
    """Return an AgentMail client initialised from settings."""
    from agentmail import AgentMail

    from app.config import get_settings

    settings = get_settings()
    return AgentMail(api_key=settings.agentmail_api_key)


def _msg_to_dataclass(msg: Any) -> AgentMailMessage:
    from_display: str = getattr(msg, "from_", "") or ""
    from_email = _norm_email(from_display)

    refs_raw = getattr(msg, "references", None) or []
    attachments_raw = getattr(msg, "attachments", None) or []
    attachments: list[dict[str, Any]] = []
    for a in attachments_raw:
        if hasattr(a, "__dict__"):
            attachments.append(a.__dict__)
        elif isinstance(a, dict):
            attachments.append(a)

    return AgentMailMessage(
        inbox_id=getattr(msg, "inbox_id", ""),
        message_id=getattr(msg, "message_id", ""),
        thread_id=getattr(msg, "thread_id", None),
        from_email=from_email,
        from_display=from_display,
        to=list(getattr(msg, "to", None) or []),
        cc=list(getattr(msg, "cc", None) or []),
        subject=getattr(msg, "subject", "") or "",
        text=getattr(msg, "text", None),
        html=getattr(msg, "html", None),
        timestamp=getattr(msg, "timestamp", None),
        in_reply_to=getattr(msg, "in_reply_to", None),
        references=list(refs_raw),
        attachments=attachments,
        headers=dict(getattr(msg, "headers", None) or {}),
        size=int(getattr(msg, "size", 0) or 0),
        labels=list(getattr(msg, "labels", None) or []),
        created_at=getattr(msg, "created_at", None),
        raw={},
    )


def fetch_message(inbox_id: str, message_id: str) -> AgentMailMessage:
    """Fetch a single full message from AgentMail."""
    client = get_client()
    msg = client.inboxes.messages.get(inbox_id=inbox_id, message_id=message_id)
    result = _msg_to_dataclass(msg)
    return result


def list_messages(
    inbox_id: str,
    limit: int = 10,
    label: Optional[str] = None,
) -> list[AgentMailMessage]:
    """List recent messages from an inbox."""
    client = get_client()
    kwargs: dict[str, Any] = {"inbox_id": inbox_id, "limit": limit}
    if label:
        kwargs["labels"] = [label]
    result = client.inboxes.messages.list(**kwargs)
    msgs = getattr(result, "messages", result) or []
    return [_msg_to_dataclass(m) for m in msgs]


def parse_auth_status(headers: dict[str, str]) -> tuple[str, str]:
    """
    Parse Authentication-Results header to derive auth_status.

    Returns (status, details_json) where status is "pass" | "fail" | "unknown".
    """
    import json
    import re

    raw = headers.get("Authentication-Results", "")
    if not raw:
        return "unknown", "{}"

    def _find(key: str) -> str:
        m = re.search(rf"{key}=(\S+)", raw, re.IGNORECASE)
        return m.group(1).rstrip(";,").lower() if m else "none"

    spf = _find("spf")
    dkim = _find("dkim")
    dmarc = _find("dmarc")

    details = {"spf": spf, "dkim": dkim, "dmarc": dmarc, "raw": raw[:500]}

    # Any explicit fail → status = fail
    if any(v == "fail" for v in (spf, dkim, dmarc)):
        return "fail", json.dumps(details)

    # At least one pass and no explicit fail → pass
    if any(v == "pass" for v in (spf, dkim, dmarc)):
        return "pass", json.dumps(details)

    return "unknown", json.dumps(details)
