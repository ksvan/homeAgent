"""Unit tests for app.webchat.ws_loop's live per-message authorize() check
— see docs/household-identity-and-access-design.md Option D: "the server
actively closing any now-unauthorized open WebSocket, not waiting for it
to notice on its next message" (for a socket that DOES send another
message; close_connections_for_user, tested separately in
test_webchat_channel.py, covers the idle-connection half of that).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

import app.webchat.ws_loop as ws_loop
from app.policy.authorize import AuthDecision
from app.webchat.channel import WebChannel
from app.webchat.session import SessionInfo


class _FakeWebSocket:
    def __init__(self, frames: list[dict[str, object]]) -> None:
        self._frames = [json.dumps(f) for f in frames]
        self.sent: list[str] = []
        self.closed_with_code: int | None = None

    async def receive_text(self) -> str:
        if not self._frames:
            from fastapi import WebSocketDisconnect

            raise WebSocketDisconnect()
        return self._frames.pop(0)

    async def send_text(self, data: str) -> None:
        self.sent.append(data)

    async def close(self, code: int = 1000) -> None:
        self.closed_with_code = code


def _session() -> SessionInfo:
    return SessionInfo(
        token="tok-1", user_id="user-1", household_id="hh-1", expires_at=datetime.now(timezone.utc)
    )


async def test_revoked_access_closes_socket_before_dispatching_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ws_loop, "load_principal", lambda user_id: object())
    monkeypatch.setattr(
        ws_loop, "authorize", lambda principal, surface: AuthDecision(False, "account_disabled")
    )

    called = {"count": 0}

    async def _fake_handle_web_message(*a: object, **k: object) -> str:
        called["count"] += 1
        return "should not run"

    monkeypatch.setattr(ws_loop, "handle_web_message", _fake_handle_web_message)

    ws = _FakeWebSocket([{"type": "message", "text": "hello"}])
    channel = WebChannel()
    session = _session()
    channel.register_connection(session.token, ws, user_id=session.user_id)  # type: ignore[arg-type]

    await ws_loop.run_chat_ws_loop(ws, session, channel, "tok-1", lambda token: None)

    assert ws.closed_with_code == 4403
    assert called["count"] == 0
    assert channel.active_connection_count() == 0


async def test_authorized_message_is_dispatched_normally(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ws_loop, "load_principal", lambda user_id: object())
    monkeypatch.setattr(ws_loop, "authorize", lambda principal, surface: AuthDecision(True, "ok"))

    async def _fake_handle_web_message(*a: object, **k: object) -> str:
        return "hi there"

    monkeypatch.setattr(ws_loop, "handle_web_message", _fake_handle_web_message)

    ws = _FakeWebSocket([{"type": "message", "text": "hello"}])
    channel = WebChannel()
    session = _session()
    channel.register_connection(session.token, ws, user_id=session.user_id)  # type: ignore[arg-type]

    touched: list[str] = []
    await ws_loop.run_chat_ws_loop(ws, session, channel, "tok-1", touched.append)

    assert ws.closed_with_code is None
    assert touched == ["tok-1"]
    assert len(ws.sent) == 1
    frame = json.loads(ws.sent[0])
    assert frame == {"type": "message", "role": "agent", "text": "hi there"}
