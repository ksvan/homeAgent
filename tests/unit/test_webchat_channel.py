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


class _FakeWebSocketWithClose(_FakeWebSocket):
    def __init__(self, fail: bool = False) -> None:
        super().__init__(fail=fail)
        self.closed_with_code: int | None = None

    async def close(self, code: int = 1000) -> None:
        self.closed_with_code = code


async def test_close_connections_for_user_closes_all_their_sessions() -> None:
    channel = WebChannel()
    ws1 = _FakeWebSocketWithClose()
    ws2 = _FakeWebSocketWithClose()
    channel.register_connection("tok-1", ws1, user_id="user-1")  # type: ignore[arg-type]
    channel.register_connection("tok-2", ws2, user_id="user-1")  # type: ignore[arg-type]

    closed = await channel.close_connections_for_user("user-1")

    assert closed == 2
    assert ws1.closed_with_code == 4403
    assert ws2.closed_with_code == 4403
    assert channel.active_connection_count() == 0


async def test_close_connections_for_user_leaves_other_users_alone() -> None:
    channel = WebChannel()
    ws_a = _FakeWebSocketWithClose()
    ws_b = _FakeWebSocketWithClose()
    channel.register_connection("tok-a", ws_a, user_id="user-a")  # type: ignore[arg-type]
    channel.register_connection("tok-b", ws_b, user_id="user-b")  # type: ignore[arg-type]

    await channel.close_connections_for_user("user-a")

    assert ws_a.closed_with_code == 4403
    assert ws_b.closed_with_code is None
    assert channel.active_connection_count() == 1


async def test_close_connections_for_user_is_a_noop_for_unknown_user() -> None:
    channel = WebChannel()
    closed = await channel.close_connections_for_user("nobody")
    assert closed == 0


async def test_close_connections_for_user_uses_custom_code() -> None:
    channel = WebChannel()
    ws = _FakeWebSocketWithClose()
    channel.register_connection("tok-1", ws, user_id="user-1")  # type: ignore[arg-type]

    await channel.close_connections_for_user("user-1", code=4401)

    assert ws.closed_with_code == 4401


def test_register_connection_without_user_id_is_invisible_to_close_for_user() -> None:
    """Backward compatibility: pre-Phase-2 call sites that don't pass
    user_id still register normally, just outside the force-close index."""
    channel = WebChannel()
    channel.register_connection("tok-1", _FakeWebSocket())  # type: ignore[arg-type]
    assert channel.active_connection_count() == 1


def test_unregister_connection_removes_user_index_entry() -> None:
    channel = WebChannel()
    ws = _FakeWebSocket()
    channel.register_connection("tok-1", ws, user_id="user-1")  # type: ignore[arg-type]
    channel.unregister_connection("tok-1", ws)  # type: ignore[arg-type]

    assert channel._tokens_by_user.get("user-1", set()) == set()


def test_connection_count_for_user_tracks_distinct_sessions() -> None:
    channel = WebChannel()
    channel.register_connection("tok-1", _FakeWebSocket(), user_id="user-1")  # type: ignore[arg-type]
    channel.register_connection("tok-2", _FakeWebSocket(), user_id="user-1")  # type: ignore[arg-type]
    channel.register_connection("tok-3", _FakeWebSocket(), user_id="user-2")  # type: ignore[arg-type]

    assert channel.connection_count_for_user("user-1") == 2
    assert channel.connection_count_for_user("user-2") == 1
    assert channel.connection_count_for_user("no-such-user") == 0


def test_connection_count_for_user_does_not_double_count_same_token() -> None:
    """Re-registering under the same token (e.g. a page refresh reusing
    the same session cookie) replaces the entry rather than adding a
    second one — matches how a real browser sharing one cookie jar
    across tabs behaves."""
    channel = WebChannel()
    channel.register_connection("tok-1", _FakeWebSocket(), user_id="user-1")  # type: ignore[arg-type]
    channel.register_connection("tok-1", _FakeWebSocket(), user_id="user-1")  # type: ignore[arg-type]

    assert channel.connection_count_for_user("user-1") == 1
