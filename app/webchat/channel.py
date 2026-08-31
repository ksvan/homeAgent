"""
WebChannel — Channel adapter for the web chat WebSocket connections.

Unlike Telegram (one long-lived bot connection, chat_id addresses any user
at any time), a web chat "channel_user_id" is a session token that is only
reachable while its browser tab holds an open WebSocket. Outbound sends are
therefore best-effort: if the session isn't currently connected, the message
is dropped (logged) rather than queued — v1 web chat is pull/session-based
only, per docs/web-chat-channel-design.md's Explicitly Deferred section.

Registered under the "web" name in app.channels.registry so mid-run policy
gate confirmations (AgentDeps.channel == "web") route here instead of to
Telegram. Proactive/scheduled sends (reminders, event rules) don't pass a
channel name and so keep resolving to the default "telegram" channel — web
chat has no proactive story yet (see design doc Decision #5).
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from app.channels.base import Channel

if TYPE_CHECKING:
    from fastapi import WebSocket

logger = logging.getLogger(__name__)


class WebChannel(Channel):
    def __init__(self) -> None:
        self._connections: dict[str, "WebSocket"] = {}
        # Reverse index for force-closing every open connection belonging
        # to a user whose access was just revoked (docs/household-identity-
        # and-access-design.md Option D: "the server actively closing any
        # now-unauthorized open WebSocket, not waiting for it to notice on
        # its next message"). One user can hold multiple sessions/tabs.
        self._tokens_by_user: dict[str, set[str]] = {}

    # ------------------------------------------------------------------
    # Connection registry — used by app.webchat.api's WS endpoint
    # ------------------------------------------------------------------

    def register_connection(self, session_token: str, ws: "WebSocket", user_id: str = "") -> None:
        """Bind a live WebSocket to a session token, replacing any prior one
        (e.g. a page refresh reconnecting under the same session). `user_id`
        is optional so existing (pre-Phase-2) call sites keep working; a
        connection registered without one is invisible to
        close_connections_for_user."""
        self._connections[session_token] = ws
        if user_id:
            self._tokens_by_user.setdefault(user_id, set()).add(session_token)

    def unregister_connection(self, session_token: str, ws: "WebSocket") -> None:
        """Remove the binding, but only if `ws` is still the registered one —
        avoids a stale disconnect handler evicting a newer reconnection."""
        if self._connections.get(session_token) is ws:
            del self._connections[session_token]
        for tokens in self._tokens_by_user.values():
            tokens.discard(session_token)

    def active_connection_count(self) -> int:
        return len(self._connections)

    async def close_connections_for_user(self, user_id: str, code: int = 4403) -> int:
        """Force-close every open connection for `user_id` (e.g. a surface
        was just disabled or the account was deactivated). Returns how many
        were closed. Best-effort: a socket that's already gone is just
        dropped from the index, not treated as an error."""
        tokens = list(self._tokens_by_user.get(user_id, ()))
        closed = 0
        for token in tokens:
            ws = self._connections.get(token)
            if ws is None:
                continue
            try:
                await ws.close(code=code)
            except Exception:
                pass
            del self._connections[token]
            closed += 1
        self._tokens_by_user.pop(user_id, None)
        return closed

    async def _send_json(self, session_token: str, payload: dict[str, object]) -> None:
        ws = self._connections.get(session_token)
        if ws is None:
            logger.info(
                "WebChannel: no live connection for session (dropping %s)",
                payload.get("type"),
            )
            return
        try:
            await ws.send_text(json.dumps(payload))
        except Exception:
            logger.warning("WebChannel: send failed — dropping connection", exc_info=True)
            self._connections.pop(session_token, None)

    # ------------------------------------------------------------------
    # Channel interface
    # ------------------------------------------------------------------

    async def send_message(self, channel_user_id: str, text: str) -> None:
        await self._send_json(channel_user_id, {"type": "message", "role": "agent", "text": text})

    async def send_confirmation_prompt(
        self,
        channel_user_id: str,
        action_description: str,
        token: str,
    ) -> None:
        await self._send_json(
            channel_user_id,
            {
                "type": "confirm_request",
                "kind": "policy",
                "token": token,
                "text": f"Confirm action: {action_description}",
            },
        )

    async def send_email_intake_prompt(
        self,
        channel_user_id: str,
        prompt_text: str,
        token: str,
    ) -> None:
        await self._send_json(
            channel_user_id,
            {
                "type": "confirm_request",
                "kind": "email",
                "token": token,
                "text": prompt_text,
            },
        )
