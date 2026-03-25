from __future__ import annotations

import logging

from fastmcp import FastMCP

from app import prom_client as pc
from app.guards import validate_metric_allowlist, validate_range
from app.models import InstantResult, RangeResult, SeriesMetadata

logger = logging.getLogger(__name__)

mcp = FastMCP(
    name="prometheus",
    instructions=(
        "Read-only access to a Prometheus metrics server. "
        "Use prom_query for current values, prom_query_range for time series trends, "
        "prom_list_metrics / prom_label_values for metric discovery, "
        "and prom_series for series metadata."
    ),
)


@mcp.tool
async def prom_query(
    query: str,
    time: str | None = None,
) -> InstantResult:
    """Run an instant PromQL query and return current metric values.

    Use this for questions like "what is the power consumption right now?"
    or "is device X online?".

    Args:
        query: PromQL expression, e.g. 'node_load1' or
               'sum(rate(http_requests_total[5m])) by (job)'.
        time:  Optional RFC3339 evaluation timestamp (default: now),
               e.g. '2024-01-15T10:30:00Z'.
    """
    validate_metric_allowlist(query)
    return await pc.query(query, time)


@mcp.tool
async def prom_query_range(
    query: str,
    start: str,
    end: str,
    step: int = 300,
) -> RangeResult:
    """Run a range PromQL query and return time series data with summary statistics.

    Use this for trend questions like "show power usage over the last 24 hours"
    or "what was the temperature in the bedroom today?".

    Each series in the result includes:
    - labels: metric label-set
    - datapoints: [[unix_timestamp, value], ...] — numeric only, NaN dropped
    - min, max, avg, latest: pre-computed summary statistics

    Args:
        query: PromQL expression.
        start: Range start (older/earlier time), RFC3339, e.g. '2024-01-15T00:00:00Z'. Must be before end.
        end:   Range end (newer/later time, usually now), RFC3339, e.g. '2024-01-16T00:00:00Z'. Must be after start.
        step:  Resolution step in seconds (default 300 = 5 min; minimum enforced by config).
    """
    validate_metric_allowlist(query)
    validate_range(start, end, step)
    return await pc.query_range(query, start, end, step)


@mcp.tool
async def prom_list_metrics(
    prefix: str | None = None,
) -> list[str]:
    """Return all metric names available in Prometheus.

    Use this to discover what metrics exist before writing a PromQL query.

    Args:
        prefix: Optional name prefix filter, e.g. 'node_' or 'process_'.
                Returns all metrics when omitted.
    """
    return await pc.list_metrics(prefix)


@mcp.tool
async def prom_label_values(
    label: str,
    match: list[str] | None = None,
) -> list[str]:
    """Return all values for a Prometheus label.

    Useful for finding what instances, jobs, rooms, or devices are tracked.

    Args:
        label: Label name, e.g. 'job', 'instance', 'room', 'device'.
        match: Optional list of series selectors to restrict results,
               e.g. ['{job="node_exporter"}'].
    """
    return await pc.label_values(label, match)


@mcp.tool
async def prom_series(
    match: list[str],
    start: str | None = None,
    end: str | None = None,
) -> list[SeriesMetadata]:
    """Return series metadata (label-sets) matching the given selectors.

    Intended for future anomaly detection and baseline jobs: call this to
    enumerate the exact series to monitor before setting up a prom_query_range
    job. Returns label-sets only — no data values.

    Args:
        match: One or more series selectors, e.g. ['{__name__=~"node_.*"}'].
        start: Optional start of the time range to search within (RFC3339).
        end:   Optional end of the time range to search within (RFC3339).
    """
    return await pc.series(match, start, end)
