"""
Trusted-proxy-aware client IP resolution — see
docs/household-identity-and-access-design.md Phase 3 ("scope trusted-proxy
header handling to that path only").

X-Forwarded-For is client-supplied and trivially spoofable; honoring it
unconditionally would let anyone set their own rate-limit identity to
whatever they like, defeating the point. It's only trusted when the
request's own direct connection came from a configured trusted proxy
address (cloudflared/the reverse proxy itself) — otherwise the direct
connecting IP is used as-is, exactly like any request would be without a
proxy in front of it.
"""

from __future__ import annotations

from app.config import get_settings


def get_client_ip(direct_ip: str | None, forwarded_for: str | None) -> str:
    settings = get_settings()
    trusted = {ip.strip() for ip in settings.webchat_trusted_proxy_ips.split(",") if ip.strip()}

    if direct_ip in trusted and forwarded_for:
        # X-Forwarded-For is a comma-separated hop chain; the first entry
        # is the original client as recorded by the nearest trusted hop.
        first_hop = forwarded_for.split(",")[0].strip()
        if first_hop:
            return first_hop

    return direct_ip or "unknown"
