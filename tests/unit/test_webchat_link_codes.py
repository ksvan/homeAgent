"""Unit tests for app.webchat.link_codes — admin-provisioned Telegram
account-linking codes. See
docs/household-identity-and-access-design.md Option B.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest
from sqlmodel import Session, select

import app.webchat.link_codes as link_codes
from app.models.cache import TelegramLinkCode


@pytest.fixture(autouse=True)
def patch_cache_session(monkeypatch: pytest.MonkeyPatch, in_memory_engine: object) -> None:
    @contextmanager
    def _session():  # type: ignore[misc]
        with Session(in_memory_engine) as s:  # type: ignore[arg-type]
            yield s

    monkeypatch.setattr(link_codes, "cache_session", _session)
    monkeypatch.setattr(link_codes, "record_audit_event", lambda *a, **k: None)


def test_create_link_code_returns_usable_code() -> None:
    info = link_codes.create_link_code("user-1", "household-1", created_by_user_id="admin")
    assert info.code
    assert info.user_id == "user-1"
    assert info.expires_at > datetime.now(timezone.utc)


def test_code_is_human_typable_and_unambiguous() -> None:
    info = link_codes.create_link_code("user-1", "household-1", created_by_user_id="admin")
    assert len(info.code) == link_codes._CODE_LENGTH
    for ambiguous in "0O1IL":
        assert ambiguous not in info.code


def test_raw_code_is_never_stored_only_its_hash() -> None:
    info = link_codes.create_link_code("user-1", "household-1", created_by_user_id="admin")
    with link_codes.cache_session() as db:
        code_hash = link_codes._hash_code(info.code)
        row = db.exec(
            select(TelegramLinkCode).where(TelegramLinkCode.code_hash == code_hash)
        ).first()
        assert row is not None
        assert row.code_hash != info.code


def test_consume_link_code_succeeds_once() -> None:
    info = link_codes.create_link_code("user-1", "household-1", created_by_user_id="admin")
    result = link_codes.consume_link_code(info.code)
    assert result is not None
    assert result.user_id == "user-1"
    assert link_codes.consume_link_code(info.code) is None


def test_consume_link_code_is_case_insensitive() -> None:
    info = link_codes.create_link_code("user-1", "household-1", created_by_user_id="admin")
    result = link_codes.consume_link_code(info.code.lower())
    assert result is not None


def test_consume_link_code_rejects_unknown_code() -> None:
    assert link_codes.consume_link_code("NOTAREALCODE") is None


def test_consume_link_code_rejects_empty_code() -> None:
    assert link_codes.consume_link_code("") is None


def test_consume_link_code_rejects_expired() -> None:
    info = link_codes.create_link_code("user-1", "household-1", created_by_user_id="admin")
    with link_codes.cache_session() as db:
        code_hash = link_codes._hash_code(info.code)
        row = db.exec(
            select(TelegramLinkCode).where(TelegramLinkCode.code_hash == code_hash)
        ).first()
        assert row is not None
        row.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        db.add(row)
        db.commit()

    assert link_codes.consume_link_code(info.code) is None


def test_creating_new_code_revokes_previous_unclaimed_one() -> None:
    first = link_codes.create_link_code("user-1", "household-1", created_by_user_id="admin")
    second = link_codes.create_link_code("user-1", "household-1", created_by_user_id="admin")

    assert link_codes.consume_link_code(first.code) is None
    assert link_codes.consume_link_code(second.code) is not None


def test_revoke_link_code_for_user_blocks_claim() -> None:
    info = link_codes.create_link_code("user-1", "household-1", created_by_user_id="admin")
    link_codes.revoke_link_code_for_user("user-1", "household-1", revoked_by_user_id="admin")
    assert link_codes.consume_link_code(info.code) is None
