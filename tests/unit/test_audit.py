"""Unit tests for app.control.audit — the durable audit log. See
docs/household-identity-and-access-design.md Goal 8.
"""

from __future__ import annotations

import json
from contextlib import contextmanager

import pytest
from sqlmodel import Session, select

import app.control.audit as audit
from app.models.cache import AuditLog


@pytest.fixture(autouse=True)
def patch_cache_session(monkeypatch: pytest.MonkeyPatch, in_memory_engine: object) -> None:
    @contextmanager
    def _session():  # type: ignore[misc]
        with Session(in_memory_engine) as s:  # type: ignore[arg-type]
            yield s

    monkeypatch.setattr(audit, "cache_session", _session)
    monkeypatch.setattr(
        "app.control.admin_events.emit_admin_event", lambda *a, **k: None, raising=False
    )


def test_record_audit_event_persists_row() -> None:
    audit.record_audit_event("webchat.login_succeeded", "household-1", actor_user_id="user-1")

    with audit.cache_session() as db:
        rows = db.exec(select(AuditLog)).all()
        assert len(rows) == 1
        assert rows[0].event_type == "webchat.login_succeeded"
        assert rows[0].household_id == "household-1"
        assert rows[0].actor_user_id == "user-1"
        assert rows[0].target_user_id is None


def test_record_audit_event_serializes_detail() -> None:
    audit.record_audit_event(
        "webchat.login_denied", "household-1", detail={"reason": "account_disabled"}
    )

    with audit.cache_session() as db:
        row = db.exec(select(AuditLog)).first()
        assert row is not None
        assert json.loads(row.detail) == {"reason": "account_disabled"}


def test_record_audit_event_does_not_raise_on_db_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def _broken_session():  # type: ignore[misc]
        raise RuntimeError("db unavailable")

    monkeypatch.setattr(audit, "cache_session", _broken_session)
    audit.record_audit_event("webchat.login_succeeded", "household-1")
