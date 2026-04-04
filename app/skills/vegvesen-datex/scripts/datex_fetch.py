#!/usr/bin/env python3
"""
Build or fetch Statens vegvesen DATEX II 3.1 HTTP GET requests.
"""

from __future__ import annotations

import argparse
import base64
import os
import sys
import urllib.error
import urllib.parse
import urllib.request


BASE_URL = "https://datex-server-get-v3-1.atlas.vegvesen.no/datexapi"

SERVICE_PATHS = {
    "situation": "GetSituation/pullsnapshotdata",
    "travel-time-data": "GetTravelTimeData/pullsnapshotdata",
    "travel-time-locations": "GetPredefinedTravelTimeLocations/pullsnapshotdata",
    "weather-data": "GetMeasuredWeatherData/pullsnapshotdata",
    "weather-sites": "GetMeasurementWeatherSiteTable/pullsnapshotdata",
    "cctv-sites": "GetCCTVSiteTable/pullsnapshotdata",
    "cctv-status": "GetCCTVStatus/pullsnapshotdata",
}


def encode_url(path: str, params: dict[str, str] | None = None) -> str:
    query = urllib.parse.urlencode(params or {})
    url = f"{BASE_URL}/{path}"
    return f"{url}?{query}" if query else url


def add_fetch_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--username", help="DATEX username or VEGVESEN_DATEX_USERNAME")
    parser.add_argument("--password", help="DATEX password or VEGVESEN_DATEX_PASSWORD")
    parser.add_argument("--if-modified-since", help="Optional If-Modified-Since header")
    parser.add_argument("--timeout", type=float, default=20.0, help="HTTP timeout in seconds")
    parser.add_argument("--print-url", action="store_true", help="Print the built URL without fetching")


def build_simple(args: argparse.Namespace) -> str:
    return encode_url(SERVICE_PATHS[args.command])


def build_situation(args: argparse.Namespace) -> str:
    path = SERVICE_PATHS["situation"]
    if args.filter:
        path = f"{path}/filter/{urllib.parse.quote(args.filter, safe='')}"
    params: dict[str, str] = {}
    if args.srti:
        params["srti"] = "True"
    return encode_url(path, params)


def require_credentials(args: argparse.Namespace) -> tuple[str, str]:
    username = args.username or os.environ.get("VEGVESEN_DATEX_USERNAME")
    password = args.password or os.environ.get("VEGVESEN_DATEX_PASSWORD")
    if username and password:
        return username, password
    raise SystemExit(
        "Live requests require DATEX credentials. Pass --username/--password or set "
        "VEGVESEN_DATEX_USERNAME and VEGVESEN_DATEX_PASSWORD."
    )


def fetch(url: str, args: argparse.Namespace) -> int:
    username, password = require_credentials(args)
    token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
    headers = {"Authorization": f"Basic {token}"}
    if args.if_modified_since:
        headers["If-Modified-Since"] = args.if_modified_since

    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=args.timeout) as response:
            body = response.read().decode("utf-8", errors="replace")
            sys.stdout.write(body)
            if not body.endswith("\n"):
                sys.stdout.write("\n")
            return 0
    except urllib.error.HTTPError as exc:
        sys.stderr.write(f"HTTP {exc.code}: {exc.reason}\n")
        payload = exc.read().decode("utf-8", errors="replace")
        if payload:
            sys.stderr.write(payload)
            if not payload.endswith("\n"):
                sys.stderr.write("\n")
        return 1
    except urllib.error.URLError as exc:
        sys.stderr.write(f"URL error: {exc.reason}\n")
        return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    situation = subparsers.add_parser("situation", help="Build or fetch GetSituation")
    add_fetch_args(situation)
    situation.add_argument("--filter", help="SituationRecord type such as Accident or MaintenanceWorks")
    situation.add_argument("--srti", action="store_true", help="Request SRTI output")
    situation.set_defaults(builder=build_situation)

    for command in (
        "travel-time-data",
        "travel-time-locations",
        "weather-data",
        "weather-sites",
        "cctv-sites",
        "cctv-status",
    ):
        sub = subparsers.add_parser(command, help=f"Build or fetch {SERVICE_PATHS[command]}")
        add_fetch_args(sub)
        sub.set_defaults(builder=build_simple)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    url = args.builder(args)
    if args.print_url:
        print(url)
        return 0
    return fetch(url, args)


if __name__ == "__main__":
    raise SystemExit(main())
