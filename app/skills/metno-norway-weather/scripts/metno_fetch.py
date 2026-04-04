#!/usr/bin/env python3
"""
Build or fetch MET.no Weather API requests with compliant defaults.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request


BASE_URL = "https://api.met.no/weatherapi"


def round_coord(value: float) -> str:
    rounded = f"{value:.4f}"
    if "." in rounded:
        rounded = rounded.rstrip("0").rstrip(".")
    return rounded


def encode_url(path: str, params: dict[str, str]) -> str:
    query = urllib.parse.urlencode(params)
    return f"{BASE_URL}{path}?{query}" if query else f"{BASE_URL}{path}"


def add_point_args(parser: argparse.ArgumentParser, include_altitude: bool = True) -> None:
    parser.add_argument("--lat", type=float, required=True, help="Latitude in decimal degrees")
    parser.add_argument("--lon", type=float, required=True, help="Longitude in decimal degrees")
    if include_altitude:
        parser.add_argument(
            "--altitude",
            type=int,
            help="Ground altitude in meters above sea level",
        )


def add_fetch_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--user-agent",
        help="Identifying User-Agent header. Required for live fetches unless METNO_USER_AGENT is set.",
    )
    parser.add_argument(
        "--if-modified-since",
        help="Optional If-Modified-Since header value",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=20.0,
        help="HTTP timeout in seconds",
    )
    parser.add_argument(
        "--print-url",
        action="store_true",
        help="Print the fully built URL without making a network request",
    )


def base_point_params(args: argparse.Namespace, include_altitude: bool = True) -> dict[str, str]:
    params = {
        "lat": round_coord(args.lat),
        "lon": round_coord(args.lon),
    }
    if include_altitude and args.altitude is not None:
        params["altitude"] = str(args.altitude)
    return params


def build_locationforecast(args: argparse.Namespace) -> str:
    path = f"/locationforecast/2.0/{args.mode}"
    return encode_url(path, base_point_params(args))


def build_nowcast(args: argparse.Namespace) -> str:
    path = f"/nowcast/2.0/{args.mode}"
    return encode_url(path, base_point_params(args))


def build_subseasonal(args: argparse.Namespace) -> str:
    path = "/subseasonal/1.0/complete"
    return encode_url(path, base_point_params(args))


def build_tidalwater(args: argparse.Namespace) -> str:
    path = "/tidalwater/1.1/"
    params = {"harbor": args.harbor}
    if args.content_type:
        params["content_type"] = args.content_type
    if args.datatype:
        params["datatype"] = args.datatype
    return encode_url(path, params)


def build_sunrise(args: argparse.Namespace) -> str:
    path = f"/sunrise/3.0/{args.kind}"
    params = base_point_params(args, include_altitude=False)
    if args.date:
        params["date"] = args.date
    if args.offset:
        params["offset"] = args.offset
    return encode_url(path, params)


def build_metalerts(args: argparse.Namespace) -> str:
    if args.cap:
        path = f"/metalerts/2.0/{args.method}.xml"
    else:
        path = f"/metalerts/2.0/{args.method}.{args.format}"

    params: dict[str, str] = {}
    for key in ("lang", "event", "incident_name", "geographic_domain", "county", "period", "sort", "cap"):
        value = getattr(args, key, None)
        if value is None:
            continue
        api_key = {
            "incident_name": "incidentName",
            "geographic_domain": "geographicDomain",
        }.get(key, key)
        params[api_key] = value

    if (args.lat is None) != (args.lon is None):
        raise SystemExit("MetAlerts coordinate lookup requires both --lat and --lon.")

    if args.lat is not None and args.lon is not None:
        params["lat"] = round_coord(args.lat)
        params["lon"] = round_coord(args.lon)

    return encode_url(path, params)


def require_user_agent(args: argparse.Namespace) -> str:
    user_agent = args.user_agent or os.environ.get("METNO_USER_AGENT")
    if user_agent:
        return user_agent
    raise SystemExit(
        "Live requests require an identifying User-Agent. Pass --user-agent "
        '"example.com team@example.com" or set METNO_USER_AGENT.'
    )


def fetch(url: str, args: argparse.Namespace) -> int:
    headers = {
        "User-Agent": require_user_agent(args),
    }
    if args.if_modified_since:
        headers["If-Modified-Since"] = args.if_modified_since

    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=args.timeout) as response:
            body = response.read()
            content_type = response.headers.get("Content-Type", "")
            if "json" in content_type:
                parsed = json.loads(body.decode("utf-8"))
                json.dump(parsed, sys.stdout, indent=2, ensure_ascii=True)
                sys.stdout.write("\n")
            else:
                sys.stdout.write(body.decode("utf-8"))
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

    locationforecast = subparsers.add_parser("locationforecast", help="Build or fetch Locationforecast 2.0")
    add_point_args(locationforecast)
    add_fetch_args(locationforecast)
    locationforecast.add_argument(
        "--mode",
        choices=("compact", "complete"),
        default="compact",
        help="Locationforecast output variant",
    )
    locationforecast.set_defaults(builder=build_locationforecast)

    nowcast = subparsers.add_parser("nowcast", help="Build or fetch Nowcast 2.0")
    add_point_args(nowcast)
    add_fetch_args(nowcast)
    nowcast.add_argument(
        "--mode",
        choices=("complete", "classic"),
        default="complete",
        help="Nowcast output variant",
    )
    nowcast.set_defaults(builder=build_nowcast)

    subseasonal = subparsers.add_parser("subseasonal", help="Build or fetch Subseasonal 1.0")
    add_point_args(subseasonal)
    add_fetch_args(subseasonal)
    subseasonal.set_defaults(builder=build_subseasonal)

    tidalwater = subparsers.add_parser("tidalwater", help="Build or fetch Tidalwater 1.1")
    add_fetch_args(tidalwater)
    tidalwater.add_argument("--harbor", required=True, help="Harbor name such as bergen or trondheim")
    tidalwater.add_argument(
        "--content-type",
        dest="content_type",
        choices=("text/plain",),
        help="Tidalwater content type",
    )
    tidalwater.add_argument(
        "--datatype",
        choices=("weathercorrection",),
        help="Tidalwater datatype",
    )
    tidalwater.set_defaults(builder=build_tidalwater)

    sunrise = subparsers.add_parser("sunrise", help="Build or fetch Sunrise 3.0")
    add_point_args(sunrise, include_altitude=False)
    add_fetch_args(sunrise)
    sunrise.add_argument("--date", help="Date in YYYY-MM-DD")
    sunrise.add_argument("--offset", help="Timezone offset such as +02:00")
    sunrise.add_argument(
        "--kind",
        choices=("sun", "moon"),
        default="sun",
        help="Which astronomical object to query",
    )
    sunrise.set_defaults(builder=build_sunrise)

    metalerts = subparsers.add_parser("metalerts", help="Build or fetch MetAlerts 2.0")
    add_fetch_args(metalerts)
    metalerts.add_argument(
        "--method",
        choices=("current", "all", "archive", "test", "test_all", "example"),
        default="current",
        help="MetAlerts method",
    )
    metalerts.add_argument(
        "--format",
        choices=("json", "xml", "rss"),
        default="json",
        help="Response format for feed-style requests",
    )
    metalerts.add_argument("--lang", choices=("no", "en"), help="Output language")
    metalerts.add_argument("--event", help="Alert event type")
    metalerts.add_argument("--incident-name", dest="incident_name", help="Incident name filter")
    metalerts.add_argument(
        "--geographic-domain",
        dest="geographic_domain",
        choices=("land", "marine"),
        help="Restrict alerts to land or marine domains",
    )
    metalerts.add_argument("--county", help="Two-digit county number")
    metalerts.add_argument("--period", help="Archive month in YYYY-MM")
    metalerts.add_argument("--sort", help="RSS sort field")
    metalerts.add_argument("--cap", help="Fetch a specific CAP message by id")
    metalerts.add_argument("--lat", type=float, help="Coordinate lookup latitude")
    metalerts.add_argument("--lon", type=float, help="Coordinate lookup longitude")
    metalerts.set_defaults(builder=build_metalerts)

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
