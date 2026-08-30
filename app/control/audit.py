"""
Durable, security-relevant event log — see
docs/household-identity-and-access-design.md Goal 8.

Distinct from app.control.events' in-memory ring buffer, which exists for
live admin-dashboard observation only and is capped/lost on restart. This
module is the durable record: logins, session/credential lifecycle, invite
issuance/use/revoke, and (later phases) permission and admin mutations.
Every call also mirrors to the live event bus (best-effort) so today's
admin dashboard keeps seeing these events in real time too — one call
site, both audiences.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from app.db import cache_session
from app.models.cache import AuditLog

logger = logging.getLogger(__name__)


def record_audit_event(
    event_type: str,
    household_id: str,
    *,
    actor_user_id: str | None = None,
    target_user_id: str | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    try:
        with cache_session() as session:
            session.add(
                AuditLog(
                    event_type=event_type,
                    household_id=household_id,
                    actor_user_id=actor_user_id,
                    target_user_id=target_user_id,
                    detail=json.dumps(detail or {}),
                )
            )
            session.commit()
    except Exception:
        # Durable audit is important but must never be the reason a login
        # or admin action itself fails.
        logger.exception("Failed to write durable audit event %s", event_type)

    try:
        from app.control.admin_events import emit_admin_event

        emit_admin_event(
            event_type,
            {
                "household_id": household_id,
                "actor_user_id": actor_user_id,
                "target_user_id": target_user_id,
                **(detail or {}),
            },
        )
    except Exception:
        pass
