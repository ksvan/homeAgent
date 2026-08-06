"""Unit tests for app.webchat.channel.WebChannel — the Channel adapter that
pushes JSON frames over a live WebSocket, keyed by session token."""

from __future__ import annotations

import json

from app.webchat.channel import WebChannel


class _FakeWebSocket:
    def __init__(self, fail: bool = False) -> None:
        self.sent: list[str] = []
        self.fail = fail

    async def send_text(self, data: str) -> None:
        if self.fail:
            raise RuntimeError("connection closed")
        self.sent.append(data)


async def test_send_message_with_no_connection_is_a_noop() -> None:
    channel = WebChannel()
    # Should not raise even though no session is registered
    await channel.send_message("unknown-token", "hello")


async def test_send_message_delivers_to_registered_connection() -> None:
    channel = WebChannel()
    ws = _FakeWebSocket()
    channel.register_connection("tok-1", ws)  # type: ignore[arg-type]

    await channel.send_message("tok-1", "hello there")

    assert len(ws.sent) == 1
    frame = json.loads(ws.sent[0])
    assert frame == {"type": "message", "role": "agent", "text": "hello there"}


async def test_send_confirmation_prompt_frame_shape() -> None:
    channel = WebChannel()
    ws = _FakeWebSocket()
    channel.register_connection("tok-1", ws)  # type: ignore[arg-type]

    await channel.send_confirmation_prompt("tok-1", "turn off the lights", "action-token")

    frame = json.loads(ws.sent[0])
    assert frame["type"] == "confirm_request"
    assert frame["kind"] == "policy"
    assert frame["token"] == "action-token"
    assert "turn off the lights" in frame["text"]


async def test_send_email_intake_prompt_frame_shape() -> None:
    channel = WebChannel()
    ws = _FakeWebSocket()
    channel.register_connection("tok-1", ws)  # type: ignore[arg-type]

    await channel.send_email_intake_prompt("tok-1", "New email from Alice", "email-token")

    frame = json.loads(ws.sent[0])
    assert frame["type"] == "confirm_request"
    assert frame["kind"] == "email"
    assert frame["token"] == "email-token"
    assert frame["text"] == "New email from Alice"


async def test_send_failure_evicts_the_connection() -> None:
    channel = WebChannel()
    ws = _FakeWebSocket(fail=True)
    channel.register_connection("tok-1", ws)  # type: ignore[arg-type]

    await channel.send_message("tok-1", "hello")

    assert channel.active_connection_count() == 0


def test_register_connection_replaces_prior_one() -> None:
    channel = WebChannel()
    first = _FakeWebSocket()
    second = _FakeWebSocket()
    channel.register_connection("tok-1", first)  # type: ignore[arg-type]
    channel.register_connection("tok-1", second)  # type: ignore[arg-type]

    assert channel.active_connection_count() == 1
    assert channel._connections["tok-1"] is second


def test_unregister_connection_only_removes_matching_instance() -> None:
    """A stale disconnect handler for a superseded connection must not evict
    a newer reconnection under the same session token."""
    channel = WebChannel()
    old_ws = _FakeWebSocket()
    new_ws = _FakeWebSocket()
    channel.register_connection("tok-1", old_ws)  # type: ignore[arg-type]
    channel.register_connection("tok-1", new_ws)  # type: ignore[arg-type]

    channel.unregister_connection("tok-1", old_ws)  # type: ignore[arg-type]

    assert channel.active_connection_count() == 1
    assert channel._connections["tok-1"] is new_ws


def test_unregister_connection_removes_when_matching() -> None:
    channel = WebChannel()
    ws = _FakeWebSocket()
    channel.register_connection("tok-1", ws)  # type: ignore[arg-type]

    channel.unregister_connection("tok-1", ws)  # type: ignore[arg-type]

    assert channel.active_connection_count() == 0


def test_active_connection_count_tracks_multiple_sessions() -> None:
    channel = WebChannel()
    channel.register_connection("tok-1", _FakeWebSocket())  # type: ignore[arg-type]
    channel.register_connection("tok-2", _FakeWebSocket())  # type: ignore[arg-type]

    assert channel.active_connection_count() == 2
