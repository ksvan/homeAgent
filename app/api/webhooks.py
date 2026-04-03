from __future__ import annotations

import asyncio
import json
import logging
import secrets
from datetime import datetime, timezone

from fastapi import APIRouter, Header, HTTPException, Request, Response

from app.config import get_settings

logger = logging.getLogger(__name__)
router = APIRouter()

_MAX_BODY_BYTES = 64 * 1024  # 64 KB — Telegram updates are typically < 10 KB


@router.post("/webhook/telegram")
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
) -> dict:  # type: ignore[type-arg]
    settings = get_settings()

    # Constant-time comparison to avoid timing side-channels
    if not secrets.compare_digest(
        x_telegram_bot_api_secret_token or "",
        settings.telegram_webhook_secret or "",
    ):
        logger.warning("Webhook rejected: invalid secret token")
        raise HTTPException(status_code=403, detail="Invalid secret token")

    # Body-size guard — reject oversized payloads before parsing
    content_length = request.headers.get("content-length")
    if content_length and int(content_length) > _MAX_BODY_BYTES:
        logger.warning("Webhook rejected: payload too large (%s bytes)", content_length)
        return Response(status_code=413)  # type: ignore[return-value]
    raw_body = await request.body()
    if len(raw_body) > _MAX_BODY_BYTES:
        logger.warning("Webhook rejected: body too large (%d bytes)", len(raw_body))
        return Response(status_code=413)  # type: ignore[return-value]

    data = json.loads(raw_body)
    channel = request.app.state.telegram_channel

    def _task_done(fut: asyncio.Future) -> None:  # type: ignore[type-arg]
        if not fut.cancelled() and (exc := fut.exception()):
            logger.error("process_update failed: %s", exc, exc_info=exc)

    asyncio.create_task(channel.process_update(data)).add_done_callback(_task_done)
    return {"ok": True}


@router.post("/webhook/homey/event")
async def homey_event_webhook(
    request: Request,
    x_homey_secret: str | None = Header(default=None),
) -> dict:  # type: ignore[type-arg]
    """
    Receive inbound events pushed from Homey Advanced Flows.

    Homey sends a POST with JSON body when a flow fires. HomeAgent validates
    the shared secret, resolves the household, and enqueues an InboundEvent
    for the dispatcher.

    Expected body fields:
      event_type   str   "device_state_change" | "flow_trigger"
      entity_id    str   Homey device UUID (or any stable identifier)
      entity_name  str   Human-readable device name (optional)
      capability   str   Capability that changed (optional)
      value        any   New capability value (optional)
      zone         str   Zone/room name (optional)
    """
    settings = get_settings()

    if not settings.event_dispatcher_enabled:
        return {"ok": True, "note": "event dispatcher disabled"}

    # Require secret only when one is configured
    if settings.homey_webhook_secret:
        if not secrets.compare_digest(
            x_homey_secret or "",
            settings.homey_webhook_secret,
        ):
            logger.warning("Homey event webhook rejected: invalid secret")
            raise HTTPException(status_code=403, detail="Invalid secret")

    content_length = request.headers.get("content-length")
    if content_length and int(content_length) > _MAX_BODY_BYTES:
        return Response(status_code=413)  # type: ignore[return-value]
    raw_body = await request.body()
    if len(raw_body) > _MAX_BODY_BYTES:
        return Response(status_code=413)  # type: ignore[return-value]

    try:
        data = json.loads(raw_body)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    # Resolve household_id server-side — single-household system
    from sqlmodel import select

    from app.db import users_session
    from app.models.users import Household

    with users_session() as session:
        household = session.exec(select(Household)).first()

    if not household:
        logger.error("Homey event webhook: no Household record found — is setup complete?")
        raise HTTPException(status_code=503, detail="Household not initialised")

    from app.control.event_bus import InboundEvent, enqueue_event

    event = InboundEvent(
        source="homey",
        event_type=str(data.get("event_type", "device_state_change")),
        household_id=household.id,
        entity_id=str(data.get("entity_id", "")),
        payload={
            "entity_name": data.get("entity_name", ""),
            "capability": data.get("capability", ""),
            "value": data.get("value"),
            "zone": data.get("zone", ""),
        },
        timestamp=datetime.now(timezone.utc),
        raw=data,
    )
    enqueue_event(event)
    return {"ok": True}
