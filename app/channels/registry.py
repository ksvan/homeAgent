from __future__ import annotations

from app.channels.base import Channel

DEFAULT_CHANNEL = "telegram"

_channels: dict[str, Channel] = {}


def register_channel(name: str, channel: Channel) -> None:
    """Register a channel adapter under `name` (called at startup)."""
    _channels[name] = channel


def set_channel(channel: Channel) -> None:
    """Backward-compatible alias: register `channel` as the default (telegram) channel."""
    register_channel(DEFAULT_CHANNEL, channel)


def get_channel(name: str = DEFAULT_CHANNEL) -> Channel | None:
    """Return the channel adapter registered under `name`, or None if not registered.

    Existing call sites that don't pass `name` keep resolving to the Telegram
    channel — proactive/scheduled sends (reminders, event rules, flight alerts)
    are Telegram-only for now. Call sites tied to a specific in-flight
    conversation (policy-gate confirmations, verify-after-write) pass
    `ctx.deps.channel` so the reply lands on the channel the request came from.
    """
    return _channels.get(name)
