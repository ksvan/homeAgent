---
name: metno-norway-weather
description: Fetch and interpret Norwegian weather data from MET Norway's open APIs. Use when Codex needs forecasts, immediate precipitation nowcasts, subseasonal outlooks, tidal water forecasts, weather warnings, or sunrise and moon data for places in Norway, and the task should be solved with api.met.no products such as Locationforecast 2.0, Nowcast 2.0, Subseasonal 1.0, Tidalwater 1.1, MetAlerts 2.0, or Sunrise 3.0.
---

# MET.no Norway Weather

## Overview

Use this skill to choose the correct `met.no` product, build compliant requests, and interpret the results for Norway-focused weather tasks.

Prefer coordinates over place names. If the user only gives a named place, resolve it to `lat` and `lon` first using local context or another approved geocoder, then call the MET API.

## Choose The Product

- Use `locationforecast/2.0` for general weather forecasts up to 9 days. Start with `/compact` unless the task needs percentile values or extra detail from `/complete`.
- Use `nowcast/2.0` for the next 2 hours, especially if the user cares about immediate rain or snow in Norway. This product is radar-aware and updated every 5 minutes.
- Use `subseasonal/1.0` for daily weather outlooks out to 21 days in the Nordic area. Prefer this when the user asks for a longer-range trend instead of a short forecast.
- Use `tidalwater/1.1` for harbor-specific water level forecasts in Norway. Use this for storm-surge adjusted water levels, not for general ocean tide explanations.
- Use `metalerts/2.0` for active warnings, warning history, or alert filtering by county, event, or coordinates.
- Use `sunrise/3.0` for sunrise, sunset, solar noon, moonrise, moonset, and related solar or lunar timing.
- Do not use `locationforecast` or `nowcast` for aviation altitude forecasts. Their altitude parameter is ground elevation, not a flight level.

## Follow MET.no Request Rules

- Send a unique, identifying `User-Agent` on live requests. Include an app or domain name and a contact address or URL.
- Use HTTPS only.
- Truncate `lat` and `lon` to at most 4 decimals before calling the API.
- Respect `Expires` and `Last-Modified` headers. Reuse cached results and prefer conditional requests with `If-Modified-Since`.
- Avoid synchronized polling. Spread repeated requests over time.
- Cache immutable resources aggressively. In particular, a MetAlerts CAP file should only be downloaded once per alert id.
- Prefer a backend proxy for browser and mobile use. MET warns against direct high-volume browser-to-API traffic, and custom-header CORS flows are not supported.

## Workflow

1. Normalize the request.
Determine whether the user wants a general forecast, a short-term precipitation answer, a 21-day outlook, a harbor water-level forecast, an alert lookup, or solar or lunar timing. Capture `lat`, `lon`, optional ground `altitude`, harbor name, language, and any date or archive period.

2. Choose the smallest useful product.
Favor `/compact` for Locationforecast, `/complete` for Subseasonal, plain text for Tidalwater, and `.json` for MetAlerts unless the task explicitly needs XML or CAP details.

3. Use the bundled helper when fetching live data or building URLs.
The script is at `/workspace/skills/metno-norway-weather/scripts/metno_fetch.py`.
Use `run_python_script` to call it (see pattern below).

4. Interpret the response at the right level.
Summarize what matters to the user instead of dumping raw JSON. Include timing, uncertainty, and any obvious caveats such as radar coverage limits or polar-night null values.

## Use The Helper Script

Call `run_python_script` with a small wrapper. Example for a live forecast fetch:

```python
import subprocess, sys
result = subprocess.run(
    [sys.executable,
     "/workspace/skills/metno-norway-weather/scripts/metno_fetch.py",
     "locationforecast", "--lat", "59.9139", "--lon", "10.7522",
     "--user-agent", "homeagent contact@example.com"],
    capture_output=True, text=True
)
print(result.stdout or result.stderr)
```

To inspect the URL without fetching (useful for debugging), add `"--print-url"` to the argument list.

Common invocations (replace coordinates and flags as needed):

- Locationforecast compact: `["locationforecast", "--lat", LAT, "--lon", LON, "--mode", "compact"]`
- Nowcast: `["nowcast", "--lat", LAT, "--lon", LON]`
- Subseasonal: `["subseasonal", "--lat", LAT, "--lon", LON]`
- Tidalwater: `["tidalwater", "--harbor", HARBOR]`
- Sunrise: `["sunrise", "--lat", LAT, "--lon", LON, "--date", DATE, "--offset", TZ_OFFSET]`
- MetAlerts: `["metalerts", "--method", "current", "--format", "json", "--county", COUNTY_CODE]`

The script rounds coordinates to 4 decimals. Always pass `--user-agent` for live requests.

## Interpret Common Responses

- For `locationforecast` and `nowcast`, inspect `properties.timeseries`. The current step is usually the first element. Use `data.instant.details` for the base values and `next_1_hours`, `next_6_hours`, or `next_12_hours` summaries when present.
- For `subseasonal`, expect daily GeoJSON forecast data with longer-horizon aggregates and percentiles. Summarize trend and spread, not single-hour precision.
- For `tidalwater`, parse the fixed-width plain text table and explain `SURGE`, `TIDE`, and `TOTAL` in meters above mean sea level. The timestamps are UTC.
- Treat `symbol_code` as a presentation-ready weather icon key. Do not call Sunrise just to decide whether icons should use day or night variants.
- For `metalerts` JSON, prefer summarizing severity, event type, affected area, onset, expiry, and any `incidentName`.
- For `sunrise`, expect GeoJSON and remember that `sunrise`, `sunset`, `moonrise`, or `moonset` may be `null` near polar night or midnight sun conditions.

## Read The Reference File When Needed

Read `/workspace/skills/metno-norway-weather/references/metno-api-reference.md` using `run_bash_command(["cat", "skills/metno-norway-weather/references/metno-api-reference.md"])` for:

- endpoint and parameter details
- product-specific caveats
- request examples
- direct links to the official documentation
