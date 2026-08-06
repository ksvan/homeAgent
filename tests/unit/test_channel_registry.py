"""Unit tests for app.channels.registry — multi-channel registration.

Telegram stays the implicit default (existing proactive-send call sites
don't pass a channel name), while explicitly-named channels (e.g. "web")
resolve independently and don't fall back silently.
"""

from __future__ import annotations

import pytest

import app.channels.registry as registry
from app.channels.base import Channel


class _FakeChannel(Channel):
    def __init__(self, label: str) -> None:
        self.label = label
        self.sent: list[tuple[str, str]] = []

    async def send_message(self, channel_user_id: str, text: str) -> None:
        self.sent.append((channel_user_id, text))

    async def send_confirmation_prompt(
        self, channel_user_id: str, action_description: str, token: str
    ) -> None:
        pass

    async def send_email_intake_prompt(
        self, channel_user_id: str, prompt_text: str, token: str
    ) -> None:
        pass


@pytest.fixture(autouse=True)
def clear_registry() -> None:
    registry._channels.clear()
    yield
    registry._channels.clear()


def test_get_channel_returns_none_when_nothing_registered() -> None:
    assert registry.get_channel() is None
    assert registry.get_channel("web") is None


def test_register_channel_resolves_by_name() -> None:
    web = _FakeChannel("web")
    registry.register_channel("web", web)

    assert registry.get_channel("web") is web
    # Telegram (the implicit default) is unaffected by registering "web"
    assert registry.get_channel() is None
    assert registry.get_channel("telegram") is None


def test_set_channel_registers_under_default_telegram_name() -> None:
    tg = _FakeChannel("telegram")
    registry.set_channel(tg)

    assert registry.get_channel() is tg
    assert registry.get_channel("telegram") is tg


def test_multiple_channels_coexist_independently() -> None:
    tg = _FakeChannel("telegram")
    web = _FakeChannel("web")
    registry.set_channel(tg)
    registry.register_channel("web", web)

    assert registry.get_channel() is tg
    assert registry.get_channel("telegram") is tg
    assert registry.get_channel("web") is web


def test_explicit_name_lookup_does_not_fall_back_to_default() -> None:
    """A confirmation destined for 'web' must not silently land on Telegram
    just because Telegram happens to be registered — that would misroute a
    confirmation to the wrong household member's channel."""
    tg = _FakeChannel("telegram")
    registry.set_channel(tg)

    assert registry.get_channel("web") is None


def test_registering_same_name_twice_replaces_the_previous_channel() -> None:
    first = _FakeChannel("first")
    second = _FakeChannel("second")
    registry.register_channel("web", first)
    registry.register_channel("web", second)

    assert registry.get_channel("web") is second
