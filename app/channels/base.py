from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class MediaAttachment:
    """Channel-agnostic representation of a binary media item (image, audio, etc.)."""

    data: bytes
    mime_type: str  # e.g. "image/jpeg", "audio/ogg", "audio/mpeg"


@dataclass
class IncomingMessage:
    """Normalised representation of an incoming message from any channel."""

    channel_user_id: str  # channel-specific user identifier (e.g. str(telegram_id))
    text: str
    channel: str  # "telegram" | "whatsapp" | ...
    raw: dict[str, object]  # original event payload for audit / debugging
    attachments: list[MediaAttachment] = field(default_factory=list)


class Channel(ABC):
    """Abstract interface that every channel adapter must implement."""

    @abstractmethod
    async def send_message(self, channel_user_id: str, text: str) -> None:
        """Send a text message to a user identified by their channel-specific ID."""

    @abstractmethod
    async def send_confirmation_prompt(
        self,
        channel_user_id: str,
        action_description: str,
        token: str,
    ) -> None:
        """
        Send an inline Yes/No confirmation prompt.

        token is the PendingAction UUID encoded in the callback payload so
        the second webhook can look up the pending action.
        Confirmation is NOT blocking — see architecture.md for the two-webhook pattern.
        """
