"""Run one bounded DNS, TCP, or HTTP(S) reachability probe.

The script deliberately has no host enumeration, port ranges, retries, or
continuous mode. It reports from the process/container's network vantage point.
"""

from __future__ import annotations

import argparse
import json
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

_MAX_TIMEOUT_SECONDS = 10.0


def parse_timeout(raw: float) -> float:
    """Validate a short timeout suitable for one interactive probe."""
    if not 0 < raw <= _MAX_TIMEOUT_SECONDS:
        raise ValueError(f"timeout must be between 0 and {_MAX_TIMEOUT_SECONDS:g} seconds")
    return raw


def parse_http_url(raw: str) -> str:
    """Accept an absolute, credential-free HTTP(S) URL."""
    parsed = urllib.parse.urlsplit(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("url must be an absolute http:// or https:// URL")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("credentialed URLs are not supported")
    return raw


def _base(kind: str, target: str, started: float) -> dict[str, Any]:
    return {
        "kind": kind,
        "target": target,
        "vantage_point": "tools-container",
        "duration_ms": round((time.monotonic() - started) * 1000),
    }


def probe_dns(host: str) -> dict[str, Any]:
    started = time.monotonic()
    result = _base("dns", host, started)
    try:
        addresses = sorted({entry[4][0] for entry in socket.getaddrinfo(host, None)})
        result.update(ok=True, addresses=addresses)
    except socket.gaierror as exc:
        result.update(ok=False, error_type="dns", error=str(exc))
    result["duration_ms"] = round((time.monotonic() - started) * 1000)
    return result


def probe_tcp(host: str, port: int, timeout: float) -> dict[str, Any]:
    started = time.monotonic()
    result = _base("tcp", f"{host}:{port}", started)
    try:
        with socket.create_connection((host, port), timeout=timeout):
            pass
        result.update(ok=True)
    except (OSError, socket.timeout) as exc:
        result.update(ok=False, error_type="tcp", error=str(exc))
    result["duration_ms"] = round((time.monotonic() - started) * 1000)
    return result


def probe_http(url: str, timeout: float) -> dict[str, Any]:
    started = time.monotonic()
    result = _base("http", url, started)
    request = urllib.request.Request(
        url,
        method="HEAD",
        headers={"User-Agent": "HomeAgent network probe"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            result.update(ok=True, status_code=response.status, final_url=response.url)
    except urllib.error.HTTPError as exc:
        result.update(ok=False, error_type="http_status", status_code=exc.code, error=str(exc))
    except (OSError, ValueError) as exc:
        result.update(ok=False, error_type="http", error=str(exc))
    result["duration_ms"] = round((time.monotonic() - started) * 1000)
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="kind", required=True)

    dns = subcommands.add_parser("dns", help="Resolve one hostname")
    dns.add_argument("--host", required=True)

    tcp = subcommands.add_parser("tcp", help="Connect once to one TCP port")
    tcp.add_argument("--host", required=True)
    tcp.add_argument("--port", type=int, required=True)
    tcp.add_argument("--timeout", type=float, default=5.0)

    http = subcommands.add_parser("http", help="Send one HTTP(S) HEAD request")
    http.add_argument("--url", required=True)
    http.add_argument("--timeout", type=float, default=5.0)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.kind == "dns":
            result = probe_dns(args.host)
        elif args.kind == "tcp":
            if not 1 <= args.port <= 65535:
                raise ValueError("port must be between 1 and 65535")
            result = probe_tcp(args.host, args.port, parse_timeout(args.timeout))
        else:
            result = probe_http(parse_http_url(args.url), parse_timeout(args.timeout))
    except ValueError as exc:
        result = {"ok": False, "error_type": "input", "error": str(exc)}
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
