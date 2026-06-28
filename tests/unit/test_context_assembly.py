"""Tests for app/agent/context.py — assemble_context() and _build_current_user_section().

All external calls (DB, world model, tasks, episodic memory) are monkeypatched so
no database or network access is needed.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Any
from unittest.mock import MagicMock

import pytest
from sqlmodel import Session

from app.agent.context import AgentContext, _build_current_user_section, assemble_context
from app.models.users import Household, User


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_user(
    user_id: str = "u1",
    household_id: str = "hh1",
    name: str = "Alice",
    is_admin: bool = False,
) -> User:
    return User(
        id=user_id,
        household_id=household_id,
        telegram_id=12345,
        name=name,
        is_admin=is_admin,
    )


def _make_member(name: str = "Alice", member_id: str = "m1") -> MagicMock:
    m = MagicMock()
    m.name = name
    m.id = member_id
    return m


# ---------------------------------------------------------------------------
# Fixtures — patch all external dependencies of assemble_context()
# ---------------------------------------------------------------------------


@pytest.fixture
def patched_context(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """
    Replace every external call inside assemble_context() with controllable stubs.
    Returns a dict of captured call args so tests can assert on them.
    """
    captured: dict[str, Any] = {}

    # Top-level imports (bound at module import time)
    monkeypatch.setattr(
        "app.agent.context.get_user_profile",
        lambda uid: captured.update(get_user_profile_uid=uid) or {"mood": "happy"},
    )
    monkeypatch.setattr(
        "app.agent.context.get_household_profile",
        lambda hid: captured.update(get_household_profile_hid=hid) or {"city": "Oslo"},
    )
    monkeypatch.setattr(
        "app.agent.context.format_profile",
        lambda profile, label: f"## {label}\n- " + "\n- ".join(f"{k}: {v}" for k, v in profile.items()),
    )
    monkeypatch.setattr(
        "app.agent.context.load_recent_messages",
        lambda uid: captured.update(load_messages_uid=uid) or [],
    )
    monkeypatch.setattr(
        "app.agent.context.get_conversation_summary",
        lambda uid: "Previous summary.",
    )
    monkeypatch.setattr(
        "app.agent.context.search_memories",
        lambda hid, query, uid: captured.update(search_query=query) or ["Memory A", "Memory B"],
    )

    # Lazy imports (resolved inside the function body on each call)
    monkeypatch.setattr(
        "app.tasks.service.get_active_task_context",
        lambda uid: "Task: buy milk",
    )
    monkeypatch.setattr(
        "app.world.formatter.format_world_model",
        lambda hid, current_user_id: f"World model for {hid}",
    )

    # _build_current_user_section — stub it out entirely for assemble_context tests
    monkeypatch.setattr(
        "app.agent.context._build_current_user_section",
        lambda hid, uid: f"## Current User\n- user_id: {uid}",
    )

    return captured


# ---------------------------------------------------------------------------
# assemble_context() — happy path
# ---------------------------------------------------------------------------


class TestAssembleContext:
    def test_returns_agent_context(self, patched_context: dict[str, Any]) -> None:
        ctx = assemble_context("u1", "hh1", "hello")
        assert isinstance(ctx, AgentContext)

    def test_user_profile_text_populated(self, patched_context: dict[str, Any]) -> None:
        ctx = assemble_context("u1", "hh1", "hello")
        assert "User Profile" in ctx.user_profile_text
        assert "mood" in ctx.user_profile_text

    def test_household_profile_text_populated(self, patched_context: dict[str, Any]) -> None:
        ctx = assemble_context("u1", "hh1", "hello")
        assert "Household Profile" in ctx.household_profile_text
        assert "Oslo" in ctx.household_profile_text

    def test_world_model_text_populated(self, patched_context: dict[str, Any]) -> None:
        ctx = assemble_context("u1", "hh1", "hello")
        assert "World model for hh1" in ctx.world_model_text

    def test_active_task_text_populated(self, patched_context: dict[str, Any]) -> None:
        ctx = assemble_context("u1", "hh1", "hello")
        assert "buy milk" in ctx.active_task_text

    def test_conversation_summary_populated(self, patched_context: dict[str, Any]) -> None:
        ctx = assemble_context("u1", "hh1", "hello")
        assert ctx.conversation_summary == "Previous summary."

    def test_relevant_memories_populated(self, patched_context: dict[str, Any]) -> None:
        ctx = assemble_context("u1", "hh1", "hello")
        assert ctx.relevant_memories == ["Memory A", "Memory B"]

    def test_recent_messages_empty_list(self, patched_context: dict[str, Any]) -> None:
        ctx = assemble_context("u1", "hh1", "hello")
        assert ctx.recent_messages == []

    def test_current_text_passed_to_search_memories(self, patched_context: dict[str, Any]) -> None:
        assemble_context("u1", "hh1", "What is the weather today?")
        assert patched_context["search_query"] == "What is the weather today?"

    def test_user_id_passed_to_profile(self, patched_context: dict[str, Any]) -> None:
        assemble_context("user-xyz", "hh1", "hi")
        assert patched_context["get_user_profile_uid"] == "user-xyz"

    def test_household_id_passed_to_household_profile(self, patched_context: dict[str, Any]) -> None:
        assemble_context("u1", "hh-abc", "hi")
        assert patched_context["get_household_profile_hid"] == "hh-abc"

    def test_current_user_section_included(self, patched_context: dict[str, Any]) -> None:
        ctx = assemble_context("u1", "hh1", "hello")
        assert "## Current User" in ctx.current_user_text

    def test_no_conversation_summary_returns_none(
        self, monkeypatch: pytest.MonkeyPatch, patched_context: dict[str, Any]
    ) -> None:
        monkeypatch.setattr("app.agent.context.get_conversation_summary", lambda uid: None)
        ctx = assemble_context("u1", "hh1", "hello")
        assert ctx.conversation_summary is None

    def test_empty_profiles_produce_empty_strings(
        self, monkeypatch: pytest.MonkeyPatch, patched_context: dict[str, Any]
    ) -> None:
        monkeypatch.setattr("app.agent.context.get_user_profile", lambda uid: {})
        monkeypatch.setattr("app.agent.context.get_household_profile", lambda hid: {})
        # format_profile returns "" for empty dicts — restore real implementation
        from app.memory.profiles import format_profile

        monkeypatch.setattr("app.agent.context.format_profile", format_profile)
        ctx = assemble_context("u1", "hh1", "hello")
        assert ctx.user_profile_text == ""
        assert ctx.household_profile_text == ""

    def test_no_memories_returns_empty_list(
        self, monkeypatch: pytest.MonkeyPatch, patched_context: dict[str, Any]
    ) -> None:
        monkeypatch.setattr("app.agent.context.search_memories", lambda hid, q, uid: [])
        ctx = assemble_context("u1", "hh1", "hello")
        assert ctx.relevant_memories == []


# ---------------------------------------------------------------------------
# _build_current_user_section() — uses real in-memory DB
# ---------------------------------------------------------------------------


@pytest.fixture
def user_db(monkeypatch: pytest.MonkeyPatch, in_memory_engine: object) -> object:
    """Patch users_session to use the in-memory engine and seed a Household row."""

    @contextmanager
    def _session():  # type: ignore[misc]
        with Session(in_memory_engine) as s:  # type: ignore[arg-type]
            yield s

    monkeypatch.setattr("app.db.users_session", _session)

    with Session(in_memory_engine) as s:  # type: ignore[arg-type]
        s.add(Household(id="hh1", name="Test Household"))
        s.commit()

    return in_memory_engine


class TestBuildCurrentUserSection:
    def test_user_not_found_returns_empty_string(
        self, monkeypatch: pytest.MonkeyPatch, user_db: object
    ) -> None:
        monkeypatch.setattr(
            "app.world.repository.WorldModelRepository.get_member_for_user",
            staticmethod(lambda hid, uid: None),
        )
        result = _build_current_user_section("hh1", "nonexistent-user")
        assert result == ""

    def test_regular_member_with_linked_world_member(
        self, monkeypatch: pytest.MonkeyPatch, user_db: object
    ) -> None:
        with Session(user_db) as s:  # type: ignore[arg-type]
            user = _make_user(user_id="u1", household_id="hh1", name="Alice", is_admin=False)
            s.add(user)
            s.commit()

        member = _make_member(name="Alice W.", member_id="m1")
        monkeypatch.setattr(
            "app.world.repository.WorldModelRepository.get_member_for_user",
            staticmethod(lambda hid, uid: member),
        )

        result = _build_current_user_section("hh1", "u1")
        assert "## Current User" in result
        assert "user_id: u1" in result
        assert "name: Alice" in result
        assert "household_member_id: m1" in result
        assert "household_member_name: Alice W." in result
        assert "role: member" in result

    def test_admin_user_shows_admin_role(
        self, monkeypatch: pytest.MonkeyPatch, user_db: object
    ) -> None:
        with Session(user_db) as s:  # type: ignore[arg-type]
            user = _make_user(user_id="u2", household_id="hh1", name="Bob", is_admin=True)
            s.add(user)
            s.commit()

        monkeypatch.setattr(
            "app.world.repository.WorldModelRepository.get_member_for_user",
            staticmethod(lambda hid, uid: None),
        )

        result = _build_current_user_section("hh1", "u2")
        assert "role: admin" in result

    def test_user_without_linked_member_shows_not_linked(
        self, monkeypatch: pytest.MonkeyPatch, user_db: object
    ) -> None:
        with Session(user_db) as s:  # type: ignore[arg-type]
            user = _make_user(user_id="u3", household_id="hh1", name="Carol")
            s.add(user)
            s.commit()

        monkeypatch.setattr(
            "app.world.repository.WorldModelRepository.get_member_for_user",
            staticmethod(lambda hid, uid: None),
        )

        result = _build_current_user_section("hh1", "u3")
        assert "household_member_id: (not linked)" in result
        assert "household_member_name: Carol" in result

    def test_linked_member_name_overrides_user_name(
        self, monkeypatch: pytest.MonkeyPatch, user_db: object
    ) -> None:
        with Session(user_db) as s:  # type: ignore[arg-type]
            user = _make_user(user_id="u4", household_id="hh1", name="Dave")
            s.add(user)
            s.commit()

        member = _make_member(name="David Smith", member_id="m4")
        monkeypatch.setattr(
            "app.world.repository.WorldModelRepository.get_member_for_user",
            staticmethod(lambda hid, uid: member),
        )

        result = _build_current_user_section("hh1", "u4")
        assert "household_member_name: David Smith" in result
