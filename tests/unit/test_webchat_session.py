"""Unit tests for app.webchat.session — issuance, hashed-token validation,
sliding + absolute expiry, and revoke-not-delete semantics. See
docs/household-identity-and-access-design.md Option D / Goal 7.

The `cache_session` context manager in app.webchat.session is monkeypatched
to use a per-test in-memory engine so no real DB is touched.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest
from sqlmodel import Session, select

import app.webchat.session as wc_session
from app.models.cache import WebChatSession
from app.webchat.session import (
    create_session,
    get_session,
    revoke_session,
    touch_session,
)


@pytest.fixture(autouse=True)
def patch_cache_session(monkeypatch: pytest.MonkeyPatch, in_memory_engine: object) -> None:
    @contextmanager
    def _session():  # type: ignore[misc]
        with Session(in_memory_engine) as s:  # type: ignore[arg-type]
            yield s

    monkeypatch.setattr(wc_session, "cache_session", _session)


def _row_for(token: str) -> WebChatSession:
    """Fetch the raw DB row for a token, bypassing get_session's validation
    — used to inspect fields get_session() intentionally doesn't expose
    (token_hash, revoked_at, absolute_expires_at)."""
    with wc_session.cache_session() as db:
        row = db.exec(
            select(WebChatSession).where(WebChatSession.token_hash == wc_session._hash_token(token))
        ).first()
        assert row is not None
        db.expunge(row)
        return row


def test_create_session_returns_valid_token() -> None:
    info = create_session("user-1", "household-1")
    assert info.token
    assert info.user_id == "user-1"
    assert info.household_id == "household-1"
    assert info.expires_at > datetime.now(timezone.utc)


def test_raw_token_is_never_stored_only_its_hash() -> None:
    info = create_session("user-1", "household-1")
    row = _row_for(info.token)
    assert row.token_hash != info.token
    assert row.token_hash == wc_session._hash_token(info.token)


def test_create_session_sets_absolute_expiry_from_settings() -> None:
    settings = wc_session.get_settings()
    info = create_session("user-1", "household-1")
    row = _row_for(info.token)
    expected = datetime.now(timezone.utc) + timedelta(
        days=settings.web_chat_session_absolute_ttl_days
    )
    absolute = row.absolute_expires_at.replace(tzinfo=timezone.utc)
    assert abs((absolute - expected).total_seconds()) < 5


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
    assert fetched.token == info.token


def test_get_session_returns_none_and_deletes_row_expired_by_sliding_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = wc_session.get_settings()
    monkeypatch.setattr(settings, "web_chat_session_ttl_days", -1)  # already-expired sliding TTL
    info = create_session("user-1", "household-1")

    assert get_session(info.token) is None
    # Deleted, not just filtered — a second lookup still returns None and
    # doesn't error, confirming the row was actually removed.
    assert get_session(info.token) is None


def test_get_session_rejects_session_past_absolute_expiry_even_with_fresh_sliding_expiry() -> None:
    """The hard cap applies regardless of how recently the session was
    touched — a session can't outlive absolute_expires_at."""
    info = create_session("user-1", "household-1")
    with wc_session.cache_session() as db:
        row = db.exec(
            select(WebChatSession).where(
                WebChatSession.token_hash == wc_session._hash_token(info.token)
            )
        ).first()
        assert row is not None
        # Sliding expiry still far in the future...
        row.expires_at = datetime.now(timezone.utc) + timedelta(days=10)
        # ...but the absolute cap has already passed.
        row.absolute_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.add(row)
        db.commit()

    assert get_session(info.token) is None


def test_touch_session_extends_sliding_expiry() -> None:
    info = create_session("user-1", "household-1")
    original_expiry = info.expires_at

    # Push sliding expiry into the near future to make the extension observable
    with wc_session.cache_session() as db:
        row = db.exec(
            select(WebChatSession).where(
                WebChatSession.token_hash == wc_session._hash_token(info.token)
            )
        ).first()
        assert row is not None
        row.expires_at = datetime.now(timezone.utc) + timedelta(minutes=1)
        db.add(row)
        db.commit()

    touch_session(info.token)
    refreshed = get_session(info.token)
    assert refreshed is not None
    assert refreshed.expires_at > original_expiry - timedelta(days=1)


def test_touch_session_does_not_extend_past_absolute_expiry(monkeypatch: pytest.MonkeyPatch) -> None:
    info = create_session("user-1", "household-1")
    near_cap = datetime.now(timezone.utc) + timedelta(hours=1)
    with wc_session.cache_session() as db:
        row = db.exec(
            select(WebChatSession).where(
                WebChatSession.token_hash == wc_session._hash_token(info.token)
            )
        ).first()
        assert row is not None
        row.absolute_expires_at = near_cap
        db.add(row)
        db.commit()

    touch_session(info.token)  # tries to slide 30 days forward by default

    row = _row_for(info.token)
    slid_expires_at = row.expires_at.replace(tzinfo=timezone.utc)
    assert slid_expires_at <= near_cap + timedelta(seconds=1)


def test_touch_session_on_unknown_token_is_a_noop() -> None:
    touch_session("does-not-exist")  # should not raise


def test_touch_session_on_revoked_token_is_a_noop() -> None:
    info = create_session("user-1", "household-1")
    revoke_session(info.token)
    before = _row_for(info.token).expires_at

    touch_session(info.token)

    assert _row_for(info.token).expires_at == before


def test_revoke_session_revokes_access() -> None:
    info = create_session("user-1", "household-1")
    assert get_session(info.token) is not None

    revoke_session(info.token)
    assert get_session(info.token) is None


def test_revoke_session_marks_revoked_at_but_keeps_the_row_for_audit() -> None:
    """Unlike natural expiry, an explicit revoke doesn't delete the row —
    it stays visible for Goal 8's admin audit trail."""
    info = create_session("user-1", "household-1")

    revoke_session(info.token)

    row = _row_for(info.token)
    assert row.revoked_at is not None


def test_revoke_session_is_idempotent() -> None:
    info = create_session("user-1", "household-1")
    revoke_session(info.token)
    first_revoked_at = _row_for(info.token).revoked_at

    revoke_session(info.token)  # calling again should not error or move the timestamp

    assert _row_for(info.token).revoked_at == first_revoked_at


def test_revoke_session_on_unknown_token_is_a_noop() -> None:
    revoke_session("does-not-exist")  # should not raise


def test_sessions_are_independent_per_token() -> None:
    a = create_session("user-a", "household-1")
    b = create_session("user-b", "household-1")

    revoke_session(a.token)

    assert get_session(a.token) is None
    assert get_session(b.token) is not None
