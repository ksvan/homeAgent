from __future__ import annotations

from app.channels.base import Channel

_active_channel: Channel | None = None


def set_channel(channel: Channel) -> None:
    """Register the active channel adapter (called at startup)."""
    global _active_channel
    _active_channel = channel


def get_channel() -> Channel | None:
    """Return the active channel adapter, or None if not yet started."""
    return _active_channel
