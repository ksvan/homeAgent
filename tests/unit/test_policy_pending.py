"""Unit tests for app.policy.pending — PendingAction persistence, focused on
the `provider` field (see app.policy.confirm's provider-dispatch fix: a
saved action must round-trip its provider so execute_pending_action can
route to the right MCP server).
"""

from __future__ import annotations

from contextlib import contextmanager

import pytest
from sqlmodel import Session

from app.policy import pending


@pytest.fixture
def db(monkeypatch: pytest.MonkeyPatch, in_memory_engine: object) -> Session:
    @contextmanager  # type: ignore[misc]
    def _session():
        with Session(in_memory_engine) as s:  # type: ignore[arg-type]
            yield s

    monkeypatch.setattr("app.policy.pending.cache_session", _session)
    with Session(in_memory_engine) as s:  # type: ignore[arg-type]
        yield s


def test_save_defaults_to_homey_provider(db: Session) -> None:
    token = pending.save_pending_action(
        household_id="hh-1",
        user_id="user-1",
        tool_name="set_light",
        tool_args={"device_id": "dev-1"},
        policy_name="lights",
    )
    action = pending.get_pending_action(token)
    assert action is not None
    assert action.provider == "homey"


def test_save_persists_oda_provider(db: Session) -> None:
    token = pending.save_pending_action(
        household_id="hh-1",
        user_id="user-1",
        tool_name="manipulate_cart",
        tool_args={"operations": []},
        policy_name="Oda manipulate_cart",
        provider="oda",
    )
    action = pending.get_pending_action(token)
    assert action is not None
    assert action.provider == "oda"
    assert action.tool_name == "manipulate_cart"


def test_get_pending_action_returns_none_when_missing(db: Session) -> None:
    assert pending.get_pending_action("nonexistent") is None
