from __future__ import annotations

import asyncio
import json
import logging
import secrets

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
