"""
Shared WebSocket message loop for the web chat channel — used by
app.webchat.api_webauthn (the only web chat router since Phase 5; the
earlier anonymous picker/bearer-token router was removed, see
docs/household-identity-and-access-design.md). Kept as its own module
rather than inlined in the router, matching the pattern used while a
second router existed, so the auth handshake and the message-dispatch
loop stay clearly separated.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable

from fastapi import WebSocket, WebSocketDisconnect

from app.config import get_settings
from app.policy.authorize import authorize
from app.policy.principal import load_principal
from app.webchat.channel import WebChannel
from app.webchat.dispatch import handle_web_cancel, handle_web_confirm, handle_web_message
from app.webchat.session import SessionInfo

logger = logging.getLogger(__name__)


async def run_chat_ws_loop(
    websocket: WebSocket,
    session: SessionInfo,
    channel: WebChannel,
    token: str,
    touch: Callable[[str], None],
) -> None:
    """Runs until the socket disconnects. Caller is responsible for the
    auth handshake (accept, register_connection) before calling this, and
    for nothing else afterward — this owns unregister on the way out."""
    from app.control.admin_events import emit_admin_event

    emit_admin_event("webchat.session_started", {"user_id": session.user_id})

    async def _status(label: str) -> None:
        try:
            await websocket.send_text(json.dumps({"type": "status", "text": label}))
        except Exception:
            pass

    try:
        max_frame_bytes = get_settings().webchat_max_ws_frame_bytes

        while True:
            raw = await websocket.receive_text()
            if len(raw.encode("utf-8")) > max_frame_bytes:
                await websocket.close(code=1009)  # ASGI/RFC 6455 "message too big"
                break
            try:
                frame = json.loads(raw)
            except (ValueError, TypeError):
                continue

            # Live per-message check (docs/household-identity-and-access-
            # design.md Option D) — the handshake-time authorize() call
            # alone isn't enough: a session that's since been revoked, or
            # an account whose web-chat access was just turned off, must
            # not keep acting for as long as this socket happens to stay
            # open.
            decision = authorize(load_principal(session.user_id), "web_chat")
            if not decision.allowed:
                await websocket.close(code=4403)
                break

            frame_type = frame.get("type")

            if frame_type == "message":
                text = str(frame.get("text", "")).strip()
                if not text:
                    continue
                touch(token)
                response = await handle_web_message(session, text, on_status=_status)
                await websocket.send_text(
                    json.dumps({"type": "message", "role": "agent", "text": response or ""})
                )
            elif frame_type in ("confirm", "cancel"):
                action_token = str(frame.get("token", ""))
                result = (
                    await handle_web_confirm(session, action_token)
                    if frame_type == "confirm"
                    else await handle_web_cancel(session, action_token)
                )
                await websocket.send_text(
                    json.dumps(
                        {
                            "type": "confirm_result",
                            "token": action_token,
                            "ok": result.ok,
                            "text": result.message,
                        }
                    )
                )
    except WebSocketDisconnect:
        pass
    finally:
        channel.unregister_connection(session.token, websocket)
        emit_admin_event("webchat.session_ended", {"user_id": session.user_id})
