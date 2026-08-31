"""Unit tests for app.policy.principal.load_principal — the DB-touching
half of building a Principal, kept out of authorize.py's pure module."""

from __future__ import annotations

from contextlib import contextmanager

import pytest
from sqlmodel import Session

import app.policy.principal as principal_module
from app.models.users import Household, User


@pytest.fixture(autouse=True)
def patch_users_session(monkeypatch: pytest.MonkeyPatch, in_memory_engine: object) -> None:
    @contextmanager
    def _session():  # type: ignore[misc]
        with Session(in_memory_engine) as s:  # type: ignore[arg-type]
            yield s

    monkeypatch.setattr("app.db.users_session", _session)

    with _session() as db:
        db.add(Household(id="hh-1", name="The Home"))
        db.add(
            User(
                id="user-1",
                household_id="hh-1",
                telegram_id=1001,
                name="Kristian",
                is_admin=True,
                is_active=False,
                telegram_enabled=False,
                web_chat_enabled=False,
            )
        )
        db.commit()


def test_load_principal_returns_none_for_unknown_user() -> None:
    assert principal_module.load_principal("no-such-user") is None


def test_load_principal_maps_all_fields_from_user() -> None:
    principal = principal_module.load_principal("user-1")
    assert principal is not None
    assert principal.user_id == "user-1"
    assert principal.is_admin is True
    assert principal.is_active is False
    assert principal.telegram_enabled is False
    assert principal.web_chat_enabled is False
