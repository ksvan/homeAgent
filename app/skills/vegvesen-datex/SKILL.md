---
name: vegvesen-datex
description: Fetch and interpret Norwegian road traffic information from Statens vegvesen DATEX II 3.1 services. Use when Codex needs traffic messages, road situations, travel times, predefined travel time routes, roadside weather observations, weather station tables, CCTV camera metadata, or CCTV status from Vegvesenet's authenticated DATEX pull services.
---

# Vegvesen DATEX

## Overview

Use this skill to choose the correct Vegvesen DATEX pull service, build authenticated HTTP GET requests, and interpret DATEX II XML responses for Norwegian road traffic tasks.

This skill targets DATEX II 3.1 pull services from Statens vegvesen. Prefer HTTP GET unless the task explicitly requires SOAP or WSDL discovery.

## Choose The Service

- Use `GetSituation` for road traffic messages such as closures, road works, accidents, detours, weather-related issues, and temporary traffic regulations.
- Use `GetTravelTimeData` for current travel times in seconds on predefined road segments.
- Use `GetPredefinedTravelTimeLocations` for the segment metadata that explains the travel time routes and locations.
- Use `GetMeasuredWeatherData` for current road weather measurements from stations along the road network.
- Use `GetMeasurementWeatherSiteTable` for weather station metadata and locations.
- Use `GetCCTVSiteTable` for camera metadata and image or video links.
- Use `GetCCTVStatus` for whether a camera is active or inactive.

## Follow Access Rules

- Use Basic Authentication on all pull services.
- Expect per-client credentials from Vegvesenet. The service is not anonymous even if the dataset listing is public.
- Keep DATEX usernames and passwords out of user-facing output and logs. Vegvesenet treats them as confidential.
- Attribute Statens vegvesen as the source when presenting DATEX-derived information.
- Do not present translated Norwegian DATEX messages as official source text. If translation is needed, present it as your own summary.
- Use `If-Modified-Since` when polling supported services. Reuse `Last-Modified` from the latest non-304 response.
- Treat `304 Not Modified` as a normal no-change response, not an error.
- Watch for `200 OK` responses that contain a "Delivery break" in the message container. That means the upstream source has not delivered fresh data and the publication may be stale.
- Expect XML from HTTP GET. Do not assume JSON.

## Workflow

1. Identify the publication type.
Decide whether the user needs situations, travel times, route metadata, road weather, station metadata, camera metadata, or camera status.

2. Build the narrowest request.
For `GetSituation`, use a DATEX filter path when the request clearly targets one situation class such as `Accident` or `MaintenanceWorks`. Use `?srti=True` only when the user explicitly wants SRTI output.

3. Prefer HTTP GET for runtime usage.
Use the helper script to build the production URL or fetch the raw XML with Basic Auth.

4. Interpret freshness and versioning correctly.
For situations, prefer the latest `situationRecordVersionTime`. If `overallEndTime` has passed, the record can usually be treated as expired. If there is no `overallEndTime`, interpret it as valid until further notice.

## Use The Helper Script

The script is at `/workspace/skills/vegvesen-datex/scripts/datex_fetch.py`.
Call it via `run_python_script` with a wrapper:

```python
import subprocess, sys, os
result = subprocess.run(
    [sys.executable,
     "/workspace/skills/vegvesen-datex/scripts/datex_fetch.py",
     "situation",
     "--username", os.environ.get("VEGVESEN_DATEX_USERNAME", ""),
     "--password", os.environ.get("VEGVESEN_DATEX_PASSWORD", "")],
    capture_output=True, text=True
)
print(result.stdout or result.stderr)
```

Common service arguments (first positional arg):

- `situation` — road events, closures, works, accidents
- `situation --filter Accident` — filter to a specific situation class
- `travel-time-data` — current travel times on predefined segments
- `travel-time-locations` — segment metadata
- `weather-data` — road weather measurements
- `weather-sites` — weather station metadata
- `cctv-sites` — camera metadata and URLs
- `cctv-status` — camera operational status

Add `"--print-url"` to any call to inspect the URL without hitting the network.
The helper supports `"--if-modified-since", TIMESTAMP` for conditional polling.

## Interpret Common Payloads

- For situations, summarize the road event, affected road or location, severity, validity window, and any measures taken.
- For travel times, combine `GetTravelTimeData` with `GetPredefinedTravelTimeLocations` when a human-readable route summary is needed.
- For weather data, distinguish between observations and site metadata. Do not expect station names in the measurement feed alone.
- For CCTV, use the site table for URLs and location context, and use the status feed to decide whether a camera should be presented as operational.
- For all XML responses, be ready for DATEX namespaces. Avoid brittle element matching that assumes no namespace prefixes.

## Read The Reference File When Needed

Read `/workspace/skills/vegvesen-datex/references/datex31-reference.md` using `run_bash_command(["cat", "skills/vegvesen-datex/references/datex31-reference.md"])` for:

- production endpoints
- update frequencies and coverage notes
- DATEX filters and status codes
- direct links to Vegvesen and DATEX documentation
