from __future__ import annotations

import asyncio
import logging
import secrets

from fastapi import APIRouter, Header, HTTPException, Request

from app.config import get_settings

logger = logging.getLogger(__name__)
router = APIRouter()


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

    data = await request.json()
    channel = request.app.state.telegram_channel

    def _task_done(fut: asyncio.Future) -> None:  # type: ignore[type-arg]
        if not fut.cancelled() and (exc := fut.exception()):
            logger.error("process_update failed: %s", exc, exc_info=exc)

    asyncio.create_task(channel.process_update(data)).add_done_callback(_task_done)
    return {"ok": True}
