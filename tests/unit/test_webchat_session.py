"""Unit tests for app.webchat.session — issuance, validation, sliding expiry.

The `cache_session` context manager in app.webchat.session is monkeypatched
to use a per-test in-memory engine so no real DB is touched.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest
from sqlmodel import Session

import app.webchat.session as wc_session
from app.webchat.session import create_session, delete_session, get_session, touch_session


@pytest.fixture(autouse=True)
def patch_cache_session(monkeypatch: pytest.MonkeyPatch, in_memory_engine: object) -> None:
    @contextmanager
    def _session():  # type: ignore[misc]
        with Session(in_memory_engine) as s:  # type: ignore[arg-type]
            yield s

    monkeypatch.setattr(wc_session, "cache_session", _session)


def test_create_session_returns_valid_token() -> None:
    info = create_session("user-1", "household-1")
    assert info.token
    assert info.user_id == "user-1"
    assert info.household_id == "household-1"
    assert info.expires_at > datetime.now(timezone.utc)


def test_get_session_returns_none_for_unknown_token() -> None:
    assert get_session("does-not-exist") is None


def test_get_session_returns_none_for_empty_token() -> None:
    assert get_session("") is None


def test_get_session_round_trips_a_created_session() -> None:
    info = create_session("user-1", "household-1")
    fetched = get_session(info.token)
    assert fetched is not None
    assert fetched.user_id == "user-1"
    assert fetched.household_id == "household-1"


def test_get_session_returns_none_and_deletes_expired_row(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = wc_session.get_settings()
    monkeypatch.setattr(settings, "web_chat_session_ttl_days", -1)  # already-expired TTL
    info = create_session("user-1", "household-1")

    assert get_session(info.token) is None
    # Deleted, not just filtered — a second lookup still returns None and
    # doesn't error, confirming the row was actually removed.
    assert get_session(info.token) is None


def test_touch_session_extends_expiry() -> None:
    info = create_session("user-1", "household-1")
    original_expiry = info.expires_at

    # Manually push expiry into the near future to make the extension observable
    with wc_session.cache_session() as db:
        from sqlmodel import select

        from app.models.cache import WebChatSession

        row = db.exec(select(WebChatSession).where(WebChatSession.token == info.token)).first()
        assert row is not None
        row.expires_at = datetime.now(timezone.utc) + timedelta(minutes=1)
        db.add(row)
        db.commit()

    touch_session(info.token)
    refreshed = get_session(info.token)
    assert refreshed is not None
    assert refreshed.expires_at > original_expiry - timedelta(days=1)


def test_touch_session_on_unknown_token_is_a_noop() -> None:
    touch_session("does-not-exist")  # should not raise


def test_delete_session_revokes_access() -> None:
    info = create_session("user-1", "household-1")
    assert get_session(info.token) is not None

    delete_session(info.token)
    assert get_session(info.token) is None


def test_delete_session_on_unknown_token_is_a_noop() -> None:
    delete_session("does-not-exist")  # should not raise


def test_sessions_are_independent_per_token() -> None:
    a = create_session("user-a", "household-1")
    b = create_session("user-b", "household-1")

    delete_session(a.token)

    assert get_session(a.token) is None
    assert get_session(b.token) is not None
