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


@router.post("/webhook/agentmail")
async def agentmail_webhook(
    request: Request,
    svix_id: str | None = Header(default=None),
    svix_timestamp: str | None = Header(default=None),
    svix_signature: str | None = Header(default=None),
) -> dict:  # type: ignore[type-arg]
    """Receive inbound messages from AgentMail via Svix-signed webhooks."""
    settings = get_settings()

    if not settings.feature_email_channel:
        raise HTTPException(status_code=404, detail="Email channel not enabled")

    if len(await request.body()) == 0:
        raise HTTPException(status_code=400, detail="Empty body")

    # Body-size guard before parsing
    content_length = request.headers.get("content-length")
    if content_length and int(content_length) > settings.email_channel_max_raw_body_bytes:
        return Response(status_code=413)  # type: ignore[return-value]
    raw_body = await request.body()
    if len(raw_body) > settings.email_channel_max_raw_body_bytes:
        return Response(status_code=413)  # type: ignore[return-value]

    # Svix signature verification — must use raw body
    if settings.agentmail_webhook_secret:
        try:
            from svix.webhooks import Webhook

            wh = Webhook(settings.agentmail_webhook_secret)
            headers_for_svix = {
                "svix-id": svix_id or "",
                "svix-timestamp": svix_timestamp or "",
                "svix-signature": svix_signature or "",
            }
            wh.verify(raw_body, headers_for_svix)
        except Exception:
            logger.warning("AgentMail webhook rejected: Svix verification failed")
            raise HTTPException(status_code=400, detail="Invalid webhook signature")

    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    event_type = payload.get("event_type", "")
    inbox_id = payload.get("inbox_id") or (payload.get("message") or {}).get("inbox_id", "")

    # Validate inbox_id
    if settings.agentmail_inbox_id and inbox_id != settings.agentmail_inbox_id:
        logger.warning(
            "AgentMail webhook: unexpected inbox_id=%s (expected %s)",
            inbox_id,
            settings.agentmail_inbox_id,
        )
        return {"ok": True, "note": "inbox_id mismatch — ignored"}

    # Only process inbound messages
    if event_type not in ("message.received", "message.received.unauthenticated"):
        return {"ok": True, "note": f"event_type {event_type!r} ignored"}

    def _task_done(fut: asyncio.Future) -> None:  # type: ignore[type-arg]
        if not fut.cancelled() and (exc := fut.exception()):
            logger.error("agentmail intake failed: %s", exc, exc_info=exc)

    asyncio.create_task(
        _ingest_agentmail_event(payload, svix_id, event_type)
    ).add_done_callback(_task_done)

    return {"ok": True}


async def _ingest_agentmail_event(
    payload: dict,  # type: ignore[type-arg]
    delivery_id: str | None,
    event_type: str,
) -> None:
    """Persist EmailMessage row and deduplicate. No side effects beyond the write."""
    import json as _json

    from app.email.agentmail_client import parse_auth_status
    from app.email.models import EmailMessage
    from app.email.repository import get_by_delivery_id, get_by_provider_ids, save

    msg_data = payload.get("message") or payload
    provider_message_id: str = msg_data.get("message_id", "")
    provider_event_id: str = payload.get("event_id", "")

    if not provider_message_id:
        logger.warning("AgentMail webhook: missing message_id in payload — dropped")
        return

    # Persistent Svix delivery deduplication
    if delivery_id:
        existing = get_by_delivery_id(delivery_id)
        if existing:
            logger.info(
                "AgentMail webhook: duplicate delivery_id=%s (message_id=%s) — skipped",
                delivery_id,
                provider_message_id,
            )
            return

    # Message-level deduplication
    existing_msg = get_by_provider_ids("agentmail", provider_message_id)
    if existing_msg:
        logger.info(
            "AgentMail webhook: duplicate message_id=%s — skipped", provider_message_id
        )
        return

    # Auto-detection: ignore auto-replies, delivery status notifications, loopbacks
    from app.config import get_settings

    settings = get_settings()
    headers: dict[str, str] = msg_data.get("headers") or {}
    auto_submitted = headers.get("Auto-Submitted", "").lower()
    precedence = headers.get("Precedence", "").lower()
    from_addr = msg_data.get("from_", "") or ""
    content_type = headers.get("Content-Type", "").lower()

    if (
        auto_submitted.startswith("auto-")
        or precedence in ("bulk", "junk", "list")
        or "delivery-status" in content_type
        or "report" in content_type
        or (
            settings.agentmail_address
            and from_addr.lower().find(settings.agentmail_address.lower()) != -1
        )
    ):
        logger.info("AgentMail webhook: auto-generated message — ignored (from=%s)", from_addr)
        return

    # Parse auth status from headers
    auth_status, auth_details_json = parse_auth_status(headers)
    # Unauthenticated event type → override to unknown
    if event_type == "message.received.unauthenticated":
        auth_status = "unknown"

    # Normalize from email
    from email.utils import parseaddr

    _, from_email = parseaddr(from_addr)
    from_email = from_email.strip().lower()

    # Build minimized provider metadata (no raw body)
    provider_metadata = {
        "event_id": provider_event_id,
        "event_type": event_type,
        "inbox_id": msg_data.get("inbox_id", ""),
        "thread_id": msg_data.get("thread_id", ""),
        "labels": msg_data.get("labels", []),
        "size": msg_data.get("size", 0),
    }

    msg = EmailMessage(
        provider="agentmail",
        provider_event_id=provider_event_id or None,
        provider_delivery_id=delivery_id,
        provider_message_id=provider_message_id,
        provider_thread_id=msg_data.get("thread_id"),
        provider_inbox_id=msg_data.get("inbox_id", ""),
        channel_user_id=from_email,
        from_email=from_email,
        to_json=_json.dumps(msg_data.get("to") or []),
        cc_json=_json.dumps(msg_data.get("cc") or []),
        subject=msg_data.get("subject") or "",
        timestamp=None,  # parsed from datetime string if needed in Phase 1b
        status="RECEIVED",
        auth_status=auth_status,
        auth_details_json=auth_details_json,
        reply_to_email=msg_data.get("reply_to") or None,
        provider_metadata_json=_json.dumps(provider_metadata),
    )

    saved = save(msg)
    logger.info(
        "AgentMail webhook: persisted EmailMessage id=%s from=%s subject=%r",
        saved.id,
        from_email,
        saved.subject[:60],
    )

    # Trigger background processing pipeline
    from app.email.service import process_email_message

    def _process_done(fut: asyncio.Future) -> None:  # type: ignore[type-arg]
        if not fut.cancelled() and (exc := fut.exception()):
            logger.error("Email processing failed (id=%s): %s", saved.id, exc, exc_info=exc)

    asyncio.create_task(process_email_message(saved)).add_done_callback(_process_done)


@router.post("/webhook/flights/{vendor}/{webhook_token}")
async def flight_webhook(
    request: Request,
    vendor: str,
    webhook_token: str,
) -> dict:  # type: ignore[type-arg]
    """Receive push alerts from flight data providers (AeroDataBox etc.)."""
    from app.config import get_settings

    settings = get_settings()
    if not settings.feature_flight_monitor:
        raise HTTPException(status_code=404, detail="Flight monitor feature not enabled")

    _FLIGHT_MAX_BODY = 256 * 1024  # 256 KB

    content_length = request.headers.get("content-length")
    if content_length and int(content_length) > _FLIGHT_MAX_BODY:
        return Response(status_code=413)  # type: ignore[return-value]
    raw_body = await request.body()
    if len(raw_body) > _FLIGHT_MAX_BODY:
        return Response(status_code=413)  # type: ignore[return-value]

    headers = dict(request.headers)

    import asyncio

    from app.flights.service import ingest_webhook

    def _task_done(fut: asyncio.Future) -> None:  # type: ignore[type-arg]
        if not fut.cancelled() and (exc := fut.exception()):
            logger.error("ingest_webhook failed: %s", exc, exc_info=exc)

    asyncio.create_task(
        ingest_webhook(vendor, webhook_token, headers, raw_body)
    ).add_done_callback(_task_done)

    return {"ok": True}
