from __future__ import annotations

from pydantic import BaseModel


# ---------------------------------------------------------------------------
# Instant query — prom_query
# ---------------------------------------------------------------------------


class InstantSample(BaseModel):
    """A single labelled data point from an instant query."""

    labels: dict[str, str]
    timestamp: float
    value: float | None  # None for NaN / non-numeric values


class InstantResult(BaseModel):
    result_type: str  # "vector" | "scalar" | "string"
    series: list[InstantSample]


# ---------------------------------------------------------------------------
# Range query — prom_query_range
# ---------------------------------------------------------------------------


class TimeSeries(BaseModel):
    """
    One labelled time series from a range query.

    datapoints: list of [unix_timestamp, value] pairs — numeric only.
    NaN / stale / non-numeric samples are dropped.

    The summary fields (min, max, avg, latest) are pre-computed to make
    conversational and anomaly-detection use cases simpler.
    """

    labels: dict[str, str]
    datapoints: list[list[float]]  # [[ts, value], ...] — numpy-friendly
    min: float | None = None
    max: float | None = None
    avg: float | None = None
    latest: float | None = None


class RangeResult(BaseModel):
    result_type: str = "matrix"
    series: list[TimeSeries]


# ---------------------------------------------------------------------------
# Series metadata — prom_series
# ---------------------------------------------------------------------------


class SeriesMetadata(BaseModel):
    """Label-set for a single matching series."""

    labels: dict[str, str]
