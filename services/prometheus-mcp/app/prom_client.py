from __future__ import annotations

import logging
import math
from typing import Any

import httpx

from app.config import get_settings
from app.guards import validate_series_count
from app.models import InstantResult, InstantSample, RangeResult, SeriesMetadata, TimeSeries

logger = logging.getLogger(__name__)


def _to_float(v: str) -> float | None:
    """Convert a Prometheus string value to float, returning None for NaN / Inf."""
    try:
        f = float(v)
        return None if (math.isnan(f) or math.isinf(f)) else f
    except (ValueError, TypeError):
        return None


def _build_client() -> httpx.AsyncClient:
    settings = get_settings()
    return httpx.AsyncClient(
        base_url=settings.prometheus_url,
        headers=settings.prom_headers(),
        timeout=settings.prom_timeout_seconds,
    )


async def _get(path: str, params: dict[str, Any]) -> Any:
    """Make a GET request to the Prometheus HTTP API and return parsed JSON data."""
    settings = get_settings()
    async with _build_client() as client:
        response = await client.get(path, params=params)

    if len(response.content) > settings.prom_max_response_bytes:
        raise ValueError(
            f"Prometheus response too large ({len(response.content)} bytes, "
            f"max {settings.prom_max_response_bytes})"
        )

    response.raise_for_status()
    body = response.json()

    if body.get("status") != "success":
        error = body.get("error", "unknown error")
        raise RuntimeError(f"Prometheus error: {error}")

    return body["data"]


def _summarise(values: list[list[Any]]) -> tuple[list[list[float]], float | None, float | None, float | None, float | None]:
    """
    Convert raw Prometheus [[ts, str_value], ...] pairs into numeric datapoints
    and compute lightweight summary statistics.
    """
    numeric: list[float] = []
    datapoints: list[list[float]] = []

    for pair in values:
        ts = float(pair[0])
        f = _to_float(str(pair[1]))
        if f is not None:
            datapoints.append([ts, f])
            numeric.append(f)

    if numeric:
        return (
            datapoints,
            min(numeric),
            max(numeric),
            sum(numeric) / len(numeric),
            numeric[-1],
        )
    return datapoints, None, None, None, None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def query(query_str: str, time: str | None = None) -> InstantResult:
    """Instant PromQL query."""
    params: dict[str, Any] = {"query": query_str}
    if time:
        params["time"] = time

    data = await _get("/api/v1/query", params)
    result_type = data.get("resultType", "vector")
    raw = data.get("result", [])

    validate_series_count(len(raw))

    samples: list[InstantSample] = []
    for item in raw:
        labels = dict(item.get("metric", {}))
        ts, val_str = item["value"]
        samples.append(InstantSample(
            labels=labels,
            timestamp=float(ts),
            value=_to_float(str(val_str)),
        ))

    logger.debug("prom_query %r → %d samples", query_str, len(samples))
    return InstantResult(result_type=result_type, series=samples)


async def query_range(query_str: str, start: str, end: str, step: int) -> RangeResult:
    """Range PromQL query returning summarised time series."""
    params: dict[str, Any] = {
        "query": query_str,
        "start": start,
        "end": end,
        "step": step,
    }

    data = await _get("/api/v1/query_range", params)
    raw = data.get("result", [])

    validate_series_count(len(raw))

    series: list[TimeSeries] = []
    for item in raw:
        labels = dict(item.get("metric", {}))
        datapoints, ts_min, ts_max, ts_avg, ts_latest = _summarise(item.get("values", []))
        series.append(TimeSeries(
            labels=labels,
            datapoints=datapoints,
            min=ts_min,
            max=ts_max,
            avg=ts_avg,
            latest=ts_latest,
        ))

    logger.debug(
        "prom_query_range %r → %d series, %d total datapoints",
        query_str,
        len(series),
        sum(len(s.datapoints) for s in series),
    )
    return RangeResult(series=series)


async def list_metrics(prefix: str | None = None) -> list[str]:
    """Return all metric names, optionally filtered by prefix."""
    data = await _get("/api/v1/label/__name__/values", {})
    names: list[str] = data if isinstance(data, list) else []
    if prefix:
        names = [n for n in names if n.startswith(prefix)]
    names.sort()
    logger.debug("list_metrics prefix=%r → %d names", prefix, len(names))
    return names


async def label_values(label: str, match: list[str] | None = None) -> list[str]:
    """Return values for a label, optionally filtered by series selector."""
    params: dict[str, Any] = {}
    if match:
        params["match[]"] = match
    data = await _get(f"/api/v1/label/{label}/values", params)
    values: list[str] = data if isinstance(data, list) else []
    logger.debug("label_values %r → %d values", label, len(values))
    return sorted(values)


async def series(
    match: list[str],
    start: str | None = None,
    end: str | None = None,
) -> list[SeriesMetadata]:
    """Return series metadata (label-sets) matching the given selectors."""
    params: dict[str, Any] = {"match[]": match}
    if start:
        params["start"] = start
    if end:
        params["end"] = end

    data = await _get("/api/v1/series", params)
    raw: list[dict[str, str]] = data if isinstance(data, list) else []

    validate_series_count(len(raw))

    result = [SeriesMetadata(labels=dict(item)) for item in raw]
    logger.debug("series match=%r → %d results", match, len(result))
    return result
