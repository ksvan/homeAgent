from __future__ import annotations

import math
from datetime import datetime


def _parse_rfc3339(t: str) -> datetime:
    """Parse RFC3339 / ISO-8601 timestamp string into a timezone-aware datetime."""
    return datetime.fromisoformat(t.replace("Z", "+00:00"))


def validate_range(start: str, end: str, step: int) -> None:
    """
    Reject range queries that exceed configured guardrails.

    Raises ValueError with a descriptive message on any violation.
    """
    from app.config import get_settings

    settings = get_settings()

    try:
        start_dt = _parse_rfc3339(start)
        end_dt = _parse_rfc3339(end)
    except ValueError as exc:
        raise ValueError(f"Invalid timestamp format: {exc}") from exc

    delta_seconds = (end_dt - start_dt).total_seconds()
    if delta_seconds <= 0:
        raise ValueError("start must be before end")

    delta_hours = delta_seconds / 3600
    if delta_hours > settings.prom_max_range_hours:
        raise ValueError(
            f"Range {delta_hours:.1f}h exceeds maximum {settings.prom_max_range_hours}h. "
            "Use a shorter window or increase PROM_MAX_RANGE_HOURS."
        )

    if step < settings.prom_min_step_seconds:
        raise ValueError(
            f"Step {step}s is below the minimum {settings.prom_min_step_seconds}s. "
            "Use a larger step value."
        )

    estimated_dp = math.ceil(delta_seconds / step)
    if estimated_dp > settings.prom_max_datapoints:
        raise ValueError(
            f"Estimated {estimated_dp} datapoints exceeds maximum {settings.prom_max_datapoints}. "
            "Increase step or shorten the range."
        )


def validate_series_count(count: int) -> None:
    """Reject results with more series than the configured maximum."""
    from app.config import get_settings

    settings = get_settings()
    if count > settings.prom_max_series:
        raise ValueError(
            f"Query returned {count} series, exceeds maximum {settings.prom_max_series}. "
            "Add label selectors to reduce cardinality."
        )


def validate_metric_allowlist(query: str) -> None:
    """
    If a metric prefix allowlist is configured, reject queries that do not
    contain any of the allowed prefixes as a substring.

    This is a simple string check — not full PromQL parsing — and is intentionally
    permissive: a prefix appearing anywhere in the query string (including in a label
    value) counts as a match. For a home lab this is sufficient.
    """
    from app.config import get_settings

    prefixes = get_settings().prom_metric_prefix_allowlist
    if not prefixes:
        return
    if not any(p in query for p in prefixes):
        raise ValueError(
            f"Query does not contain any allowed metric prefix: {prefixes}. "
            "Add your metric prefix to PROM_METRIC_PREFIX_ALLOWLIST or leave it empty to allow all."
        )
