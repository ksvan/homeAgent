"""
Web chat HTTP + WebSocket API.

LAN-only, no admin-token auth — the trust boundary is "on the network and
picked a household user from the list" (see docs/web-chat-channel-design.md
"Identity & login"). Session bearer tokens gate everything past login.
"""

from __future__ import annotations

import json
import logging
import pathlib
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from app.webchat.channel import WebChannel
from app.webchat.dispatch import handle_web_cancel, handle_web_confirm, handle_web_message
from app.webchat.session import (
    SessionInfo,
    create_session,
    delete_session,
    get_session,
    touch_session,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["webchat"])

# Module-level singleton so app.api.server's lifespan can register the same
# instance under "web" in app.channels.registry — mid-run policy gate
# confirmations need to reach this exact connection map.
_channel = WebChannel()


def get_web_channel() -> WebChannel:
    return _channel


async def _require_session(authorization: str | None = Header(default=None)) -> SessionInfo:
    token = ""
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:]
    session = get_session(token)
    if session is None:
        raise HTTPException(status_code=401, detail="Invalid or expired session")
    touch_session(token)
    return session


class SelectUserRequest(BaseModel):
    user_id: str


@router.get("/", response_class=HTMLResponse)
async def index() -> str:
    path = pathlib.Path(__file__).with_name("static") / "chat.html"
    try:
        return path.read_text()
    except FileNotFoundError:
        logger.error("chat.html not found next to app/webchat/api.py — web chat UI unavailable")
        return "<html><body><pre>Web chat UI unavailable: chat.html missing.</pre></body></html>"


@router.get("/api/users")
async def list_users() -> dict[str, Any]:
    """Household user picker list — no auth, matches 'pick yourself from the
    household's existing user list' (LAN trust model, no password)."""
    from sqlmodel import select

    from app.db import users_session
    from app.models.users import User

    with users_session() as db:
        users = db.exec(select(User).order_by(User.name)).all()

    return {"users": [{"id": u.id, "name": u.name, "household_id": u.household_id} for u in users]}


@router.post("/api/session")
async def start_session(body: SelectUserRequest) -> dict[str, Any]:
    from sqlmodel import select

    from app.db import users_session
    from app.models.users import User

    with users_session() as db:
        user = db.exec(select(User).where(User.id == body.user_id)).first()
    if user is None:
        raise HTTPException(status_code=404, detail="Unknown user")

    info = create_session(user.id, user.household_id)
    return {
        "token": info.token,
        "user_id": user.id,
        "name": user.name,
        "expires_at": info.expires_at.isoformat(),
    }


@router.get("/api/me")
async def me(session: SessionInfo = Depends(_require_session)) -> dict[str, Any]:
    from sqlmodel import select

    from app.db import users_session
    from app.models.users import User

    with users_session() as db:
        user = db.exec(select(User).where(User.id == session.user_id)).first()

    return {
        "user_id": session.user_id,
        "name": user.name if user else "",
        "expires_at": session.expires_at.isoformat(),
    }


@router.delete("/api/session")
async def end_session(session: SessionInfo = Depends(_require_session)) -> dict[str, bool]:
    delete_session(session.token)
    return {"ok": True}


@router.websocket("/ws")
async def chat_ws(websocket: WebSocket, token: str = "") -> None:
    session = get_session(token)
    if session is None:
        await websocket.close(code=4401)
        return

    await websocket.accept()
    touch_session(token)
    _channel.register_connection(session.token, websocket)

    from app.control.admin_events import emit_admin_event

    emit_admin_event("webchat.session_started", {"user_id": session.user_id})

    async def _status(label: str) -> None:
        try:
            await websocket.send_text(json.dumps({"type": "status", "text": label}))
        except Exception:
            pass

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                frame = json.loads(raw)
            except (ValueError, TypeError):
                continue

            frame_type = frame.get("type")

            if frame_type == "message":
                text = str(frame.get("text", "")).strip()
                if not text:
                    continue
                touch_session(token)
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
        _channel.unregister_connection(session.token, websocket)
        emit_admin_event("webchat.session_ended", {"user_id": session.user_id})
