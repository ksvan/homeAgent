"""Tests for app.control.api._aggregate_run_stats — token/cache/latency aggregation.

See docs/prompt-caching-design.md § Instrumentation: raw `input_tokens` from
Anthropic is the uncached remainder only, so any "total input" figure must
sum input + cache_read + cache_write, and an overall average latency masks
the cache-read/cache-write/no-cache split.
"""
from __future__ import annotations

import json

from app.control.api import _aggregate_run_stats
from app.models.cache import AgentRunLog


def _run(
    model_used: str = "claude-sonnet-5",
    input_tokens: int = 100,
    output_tokens: int = 20,
    cache_read: int = 0,
    cache_write: int = 0,
    duration_ms: int = 1000,
    tools_called: list[dict[str, object]] | None = None,
) -> AgentRunLog:
    return AgentRunLog(
        household_id="hh1",
        user_id="u1",
        model_used=model_used,
        duration_ms=duration_ms,
        tools_called=json.dumps(tools_called or []),
        tokens_used=json.dumps(
            {
                "input": input_tokens,
                "output": output_tokens,
                "cache_read": cache_read,
                "cache_write": cache_write,
            }
        ),
    )


def test_empty_runs_returns_zeroed_stats() -> None:
    stats = _aggregate_run_stats([])
    assert stats["tokens"]["input"] == 0
    assert stats["tokens"]["cached_input_share"] == 0.0
    assert stats["avg_duration_ms"] == 0
    assert stats["tokens_by_model"] == {}


def test_total_input_with_cache_sums_all_three_fields() -> None:
    runs = [_run(input_tokens=100, cache_read=850, cache_write=0)]
    stats = _aggregate_run_stats(runs)
    # This is the regression this instrumentation exists to prevent: raw
    # "input" alone would show 100, hiding that the real prompt was 950 tokens.
    assert stats["tokens"]["total_input_with_cache"] == 950
    assert stats["tokens"]["input"] == 100
    assert stats["tokens"]["cache_read"] == 850


def test_cached_input_share_is_weighted_not_a_hit_count() -> None:
    # One run with a small cache hit, one run with none — share should
    # reflect token volume, not "1 of 2 runs had any cache activity".
    runs = [
        _run(input_tokens=10, cache_read=990),
        _run(input_tokens=1000, cache_read=0),
    ]
    stats = _aggregate_run_stats(runs)
    # total_input_with_cache = 10+990 + 1000+0 = 2000; cache_read total = 990
    assert stats["tokens"]["total_input_with_cache"] == 2000
    assert stats["tokens"]["cached_input_share"] == 0.495


def test_tokens_by_model_breaks_down_per_model() -> None:
    runs = [
        _run(model_used="claude-sonnet-5", input_tokens=100, cache_read=900),
        _run(model_used="gpt-5.6", input_tokens=500, cache_read=0),
    ]
    stats = _aggregate_run_stats(runs)
    assert stats["tokens_by_model"]["claude-sonnet-5"]["cache_read"] == 900
    assert stats["tokens_by_model"]["claude-sonnet-5"]["cached_input_share"] == 0.9
    assert stats["tokens_by_model"]["gpt-5.6"]["cache_read"] == 0
    assert stats["tokens_by_model"]["gpt-5.6"]["cached_input_share"] == 0.0


def test_latency_split_by_cache_state() -> None:
    runs = [
        _run(cache_read=500, cache_write=0, duration_ms=200),  # cache hit — fast
        _run(cache_read=0, cache_write=500, duration_ms=800),  # cache write — slow (first hit)
        _run(cache_read=0, cache_write=0, duration_ms=500),  # no cache at all
    ]
    stats = _aggregate_run_stats(runs)
    latency = stats["latency_by_cache_state"]
    assert latency["cache_read_avg_ms"] == 200
    assert latency["cache_read_count"] == 1
    assert latency["cache_write_avg_ms"] == 800
    assert latency["cache_write_count"] == 1
    assert latency["no_cache_avg_ms"] == 500
    assert latency["no_cache_count"] == 1
    # An overall average would have hidden this split entirely.
    assert stats["avg_duration_ms"] == 500


def test_tool_counts_still_aggregated() -> None:
    runs = [
        _run(tools_called=[{"tool": "get_weather"}, {"tool": "get_weather"}]),
        _run(tools_called=[{"tool": "search"}]),
    ]
    stats = _aggregate_run_stats(runs)
    assert stats["tool_counts"] == {"get_weather": 2, "search": 1}


def test_malformed_tokens_json_does_not_crash_aggregation() -> None:
    bad_run = AgentRunLog(
        household_id="hh1",
        user_id="u1",
        model_used="claude-sonnet-5",
        duration_ms=100,
        tools_called="not json",
        tokens_used="not json either",
    )
    stats = _aggregate_run_stats([bad_run])
    assert stats["tokens"]["input"] == 0
    assert stats["model_counts"] == {"claude-sonnet-5": 1}
