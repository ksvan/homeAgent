# MET.no API Reference

## Routing Guide

- Use `locationforecast/2.0` for regular weather forecasts up to 9 days.
- Use `nowcast/2.0` for the next 2 hours when immediate precipitation matters.
- Use `subseasonal/1.0` for daily outlooks out to 21 days.
- Use `tidalwater/1.1` for harbor water-level forecasts in Norway.
- Use `metalerts/2.0` for active, recent, test, or archived warnings.
- Use `sunrise/3.0` for sunrise, sunset, solar noon, moonrise, and moonset.

## Shared Rules

- Use `https://api.met.no/...`.
- Send an identifying `User-Agent`. MET explicitly blocks missing or generic headers such as `okhttp`, `Dalvik`, `fhttp`, and `Java`.
- Truncate coordinates to at most 4 decimals.
- Respect `Expires`, `Last-Modified`, and `If-Modified-Since`.
- Avoid frequent polling and synchronized bursts.
- Prefer a caching backend proxy for production browser or mobile usage.

## Locationforecast 2.0

Best for general forecasts for any point on earth. For Norway use this as the default forecast product unless the user specifically asks about the next 2 hours of rain or snow.

- Base docs: `https://api.met.no/weatherapi/locationforecast/2.0/documentation`
- Methods:
  - `/compact`
  - `/complete`
  - `/classic` for legacy XML only
- Parameters:
  - `lat` required
  - `lon` required
  - `altitude` optional but recommended; ground elevation in meters
- Notes:
  - Forecast horizon is 9 days.
  - `altitude` is ground elevation only. Do not use it for above-ground forecasts.
  - `symbol_code` maps directly to MET/Yr weather icon filenames.
  - `complete` includes extra detail such as percentile values.

Examples:

```text
https://api.met.no/weatherapi/locationforecast/2.0/compact?lat=59.9139&lon=10.7522
https://api.met.no/weatherapi/locationforecast/2.0/complete?lat=60.3929&lon=5.3242&altitude=12
```

## Nowcast 2.0

Best for immediate conditions in Norway and the wider Nordic radar domain. Use this when the user wants a short-horizon answer such as "Will it rain in Oslo in the next hour?"

- Base docs: `https://api.met.no/weatherapi/nowcast/2.0/documentation`
- Methods:
  - `/complete`
  - `/classic` for legacy XML
- Parameters:
  - `lat` required
  - `lon` required
  - `altitude` optional but recommended
- Notes:
  - Forecast horizon is 2 hours.
  - Forecasts are updated every 5 minutes.
  - Coverage depends on radar availability and topography.
  - Use the status endpoint for operational checks.

Examples:

```text
https://api.met.no/weatherapi/nowcast/2.0/complete?lat=59.9139&lon=10.7522
https://api.met.no/weatherapi/nowcast/2.0/status
https://api.met.no/weatherapi/nowcast/2.0/coverage
```

## Subseasonal 1.0

Best for longer-range daily outlooks in Norway and the wider Nordic area. Use this when the user asks for trend-level guidance over the next 2 to 3 weeks.

- Base docs: `https://api.met.no/weatherapi/subseasonal/1.0/documentation`
- Method:
  - `/complete`
- Parameters:
  - `lat` required
  - `lon` required
  - `altitude` optional but recommended
- Notes:
  - Forecast horizon is 21 days.
  - Output is GeoJSON.
  - Forecasts are updated every hour.
  - This product is daily and aggregated. Do not present it as hour-by-hour precision.
  - The data model includes daily aggregates and uncertainty bands such as precipitation and temperature percentiles.

Examples:

```text
https://api.met.no/weatherapi/subseasonal/1.0/complete?lat=59.9333&lon=10.7166
https://api.met.no/weatherapi/subseasonal/1.0/complete?lat=70.3705&lon=31.0241
https://api.met.no/weatherapi/subseasonal/1.0/status
https://api.met.no/weatherapi/subseasonal/1.0/coverage
```

## Tidalwater 1.1

Best for harbor-specific water-level forecasts in Norway. Use this for sea-level and surge conditions in named harbors, not for arbitrary latitude and longitude points.

- Base docs: `https://api.met.no/weatherapi/tidalwater/1.1/documentation`
- Method:
  - root endpoint `/?harbor=<name>`
- Parameters:
  - `harbor` required
  - `content_type` optional; `text/plain` is the only valid value
  - `datatype` optional; `weathercorrection` is the only valid value
- Notes:
  - Data is returned as fixed-column plain text.
  - Forecast horizon is about 3 days.
  - Resolution is 10 minutes.
  - Values are meters above mean sea level.
  - Times are UTC.
  - Key columns:
    - `SURGE` weather contribution from wind and air pressure
    - `TIDE` astronomical tide contribution
    - `TOTAL` combined water level
    - `0p/25p/50p/75p/100p` percentiles for surge
  - Harbor coverage is limited to MET's published harbor list and should be validated against `/available`.

Examples:

```text
https://api.met.no/weatherapi/tidalwater/1.1/?harbor=bergen
https://api.met.no/weatherapi/tidalwater/1.1/?harbor=trondheim&content_type=text/plain&datatype=weathercorrection
https://api.met.no/weatherapi/tidalwater/1.1/available
```

## MetAlerts 2.0

Best for weather warnings in Norway. Prefer `.json` for automation unless the task specifically needs RSS or raw CAP XML.

- Base docs: `https://api.met.no/weatherapi/metalerts/2.0/documentation`
- Main methods:
  - `/current`
  - `/all`
  - `/archive`
  - `/test`
  - `/test_all`
  - `/example`
- Formats:
  - `.json`
  - `.xml`
  - `.rss`
- Useful parameters:
  - `lang=no|en`
  - `event=blowingSnow|forestFire|gale|ice|icing|lightning|polarLow|rain|rainFlood|snow|stormSurge|wind`
  - `incidentName`
  - `geographicDomain=land|marine`
  - `county=<two-digit Norwegian county code>`
  - `lat` and `lon` for coordinate search
  - `period=YYYY-MM` for archive
  - `sort` for RSS ordering
  - `cap=<alert id>` to fetch a specific CAP XML message
- Notes:
  - Cache CAP files by id. The docs explicitly say not to redownload them repeatedly.
  - `current` returns active alerts; `all` includes active and inactive alerts from the last 30 days.
  - The GeoJSON/JSON output is documented as supplemental and slower than the core RSS/CAP flow, but it is usually the most convenient format for agent work.
  - County codes must be zero-padded where applicable.

Examples:

```text
https://api.met.no/weatherapi/metalerts/2.0/current.json?lang=en
https://api.met.no/weatherapi/metalerts/2.0/current.json?lat=59.9139&lon=10.7522
https://api.met.no/weatherapi/metalerts/2.0/all.xml?county=03
https://api.met.no/weatherapi/metalerts/2.0/archive.json?period=2025-12&geographicDomain=land
```

## Sunrise 3.0

Best for solar and lunar event timing. This is especially important in Norway because polar-night and midnight-sun behavior can produce `null` rise or set values.

- Base docs: `https://api.met.no/weatherapi/sunrise/3.0/documentation`
- Methods:
  - `/sun`
  - `/moon`
- Parameters:
  - `lat` required
  - `lon` required
  - `date=YYYY-MM-DD` optional but strongly recommended
  - `offset=+HH:MM|-HH:MM` optional; use it when the user wants local timestamps
- Notes:
  - Output is GeoJSON.
  - `sunrise`, `sunset`, `moonrise`, or `moonset` can be `null`.
  - Use a real offset for Norway on the requested date, since DST changes the returned timestamps.

Examples:

```text
https://api.met.no/weatherapi/sunrise/3.0/sun?lat=59.9139&lon=10.7522&date=2026-04-04&offset=+02:00
https://api.met.no/weatherapi/sunrise/3.0/moon?lat=69.6492&lon=18.9553&date=2026-12-01&offset=+01:00
```

## Official Sources

- Interface index: `https://api.met.no/weatherapi/documentation`
- General usage: `https://docs.api.met.no/doc/usage.html`
- Terms of service: `https://api.met.no/doc/TermsOfService`
