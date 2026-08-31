"""Unit tests for app.webchat.client_ip.get_client_ip — trusted-proxy-aware
client IP resolution. See
docs/household-identity-and-access-design.md Phase 3.
"""

from __future__ import annotations

import pytest

from app.webchat.client_ip import get_client_ip


@pytest.fixture(autouse=True)
def trusted_proxies(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "webchat_trusted_proxy_ips", "127.0.0.1,::1")


def test_uses_direct_ip_when_no_forwarded_header() -> None:
    assert get_client_ip("203.0.113.5", None) == "203.0.113.5"


def test_ignores_forwarded_header_from_untrusted_direct_ip() -> None:
    """A public client can set X-Forwarded-For itself — it must not be
    honored unless the request's own direct connection came from a
    configured trusted proxy."""
    assert get_client_ip("203.0.113.5", "9.9.9.9") == "203.0.113.5"


def test_honors_forwarded_header_from_trusted_proxy() -> None:
    assert get_client_ip("127.0.0.1", "198.51.100.7") == "198.51.100.7"


def test_uses_first_hop_of_forwarded_chain() -> None:
    assert get_client_ip("127.0.0.1", "198.51.100.7, 10.0.0.1, 10.0.0.2") == "198.51.100.7"


def test_falls_back_to_direct_ip_when_trusted_but_header_empty() -> None:
    assert get_client_ip("127.0.0.1", None) == "127.0.0.1"


def test_falls_back_to_direct_ip_when_trusted_but_header_blank() -> None:
    assert get_client_ip("127.0.0.1", "   ") == "127.0.0.1"


def test_ipv6_loopback_is_trusted_by_default() -> None:
    assert get_client_ip("::1", "198.51.100.7") == "198.51.100.7"


def test_none_direct_ip_returns_unknown_placeholder() -> None:
    assert get_client_ip(None, None) == "unknown"
