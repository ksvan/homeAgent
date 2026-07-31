"""Tests for app.agent.runner._write_run_log — cache token persistence.

Confirms cache_read/cache_write/static_prompt_cache_version land in the
AgentRunLog.tokens_used JSON payload, per docs/prompt-caching-design.md.
"""
from __future__ import annotations

import json
from contextlib import contextmanager
from typing import Generator

import pytest
from sqlmodel import Session, select

from app.agent.runner import _write_run_log
from app.models.cache import AgentRunLog


@pytest.fixture
def cache_db(monkeypatch: pytest.MonkeyPatch, in_memory_engine: object) -> Session:
    """Monkeypatch app.db.cache_session to use the in-memory engine."""

    @contextmanager
    def _session() -> Generator[Session, None, None]:
        with Session(in_memory_engine) as s:  # type: ignore[arg-type]
            yield s

    monkeypatch.setattr("app.db.cache_session", _session)
    with Session(in_memory_engine) as s:  # type: ignore[arg-type]
        yield s


def _fetch_log(session: Session) -> AgentRunLog:
    rows = session.exec(select(AgentRunLog)).all()
    assert len(rows) == 1
    return rows[0]


def test_write_run_log_persists_cache_tokens(cache_db: Session) -> None:
    _write_run_log(
        household_id="hh1",
        user_id="u1",
        model_used="claude-sonnet-5",
        input_summary="hello",
        output_summary="hi",
        tools_called=[],
        duration_ms=123,
        input_tokens=100,
        output_tokens=20,
        cache_read_tokens=850,
        cache_write_tokens=0,
    )

    with Session(cache_db.get_bind()) as fresh:  # type: ignore[arg-type]
        log = _fetch_log(fresh)
        tokens = json.loads(log.tokens_used)

    assert tokens["input"] == 100
    assert tokens["output"] == 20
    assert tokens["cache_read"] == 850
    assert tokens["cache_write"] == 0
    assert "static_prompt_cache_version" in tokens


def test_write_run_log_defaults_cache_tokens_to_zero(cache_db: Session) -> None:
    _write_run_log(
        household_id="hh1",
        user_id="u1",
        model_used="gpt-5.6",
        input_summary="hello",
        output_summary="hi",
        tools_called=[],
        duration_ms=50,
        input_tokens=10,
        output_tokens=5,
    )

    with Session(cache_db.get_bind()) as fresh:  # type: ignore[arg-type]
        log = _fetch_log(fresh)
        tokens = json.loads(log.tokens_used)

    assert tokens["cache_read"] == 0
    assert tokens["cache_write"] == 0
