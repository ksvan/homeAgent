"""Unit tests for app.webchat.invites — admin-provisioned enrollment
invites. See docs/household-identity-and-access-design.md Option A.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest
from sqlmodel import Session, select

import app.webchat.invites as invites
from app.models.cache import WebChatInvite


@pytest.fixture(autouse=True)
def patch_cache_session(monkeypatch: pytest.MonkeyPatch, in_memory_engine: object) -> None:
    @contextmanager
    def _session():  # type: ignore[misc]
        with Session(in_memory_engine) as s:  # type: ignore[arg-type]
            yield s

    monkeypatch.setattr(invites, "cache_session", _session)
    monkeypatch.setattr(invites, "record_audit_event", lambda *a, **k: None)


def test_create_invite_returns_usable_token() -> None:
    info = invites.create_invite("user-1", "household-1", created_by_user_id="admin")
    assert info.token
    assert info.user_id == "user-1"
    assert info.expires_at > datetime.now(timezone.utc)


def test_raw_token_is_never_stored_only_its_hash() -> None:
    info = invites.create_invite("user-1", "household-1", created_by_user_id="admin")
    with invites.cache_session() as db:
        token_hash = invites._hash_token(info.token)
        row = db.exec(select(WebChatInvite).where(WebChatInvite.token_hash == token_hash)).first()
        assert row is not None
        assert row.token_hash != info.token


def test_get_valid_invite_round_trips() -> None:
    info = invites.create_invite("user-1", "household-1", created_by_user_id="admin")
    looked_up = invites.get_valid_invite(info.token)
    assert looked_up is not None
    assert looked_up.user_id == "user-1"


def test_get_valid_invite_rejects_unknown_token() -> None:
    assert invites.get_valid_invite("not-a-real-token") is None


def test_get_valid_invite_rejects_expired() -> None:
    info = invites.create_invite("user-1", "household-1", created_by_user_id="admin")
    with invites.cache_session() as db:
        token_hash = invites._hash_token(info.token)
        row = db.exec(select(WebChatInvite).where(WebChatInvite.token_hash == token_hash)).first()
        assert row is not None
        row.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        db.add(row)
        db.commit()

    assert invites.get_valid_invite(info.token) is None


def test_mark_invite_used_succeeds_once() -> None:
    info = invites.create_invite("user-1", "household-1", created_by_user_id="admin")
    assert invites.mark_invite_used(info.token) is True
    assert invites.mark_invite_used(info.token) is False


def test_mark_invite_used_blocks_further_lookup() -> None:
    info = invites.create_invite("user-1", "household-1", created_by_user_id="admin")
    invites.mark_invite_used(info.token)
    assert invites.get_valid_invite(info.token) is None


def test_mark_invite_used_rejects_unknown_token() -> None:
    assert invites.mark_invite_used("not-a-real-token") is False


def test_creating_new_invite_revokes_previous_unclaimed_one() -> None:
    first = invites.create_invite("user-1", "household-1", created_by_user_id="admin")
    second = invites.create_invite("user-1", "household-1", created_by_user_id="admin")

    assert invites.get_valid_invite(first.token) is None
    assert invites.get_valid_invite(second.token) is not None


def test_revoke_invite_for_user_blocks_claim() -> None:
    info = invites.create_invite("user-1", "household-1", created_by_user_id="admin")
    invites.revoke_invite_for_user("user-1", "household-1", revoked_by_user_id="admin")
    assert invites.get_valid_invite(info.token) is None
