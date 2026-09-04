"""Phase 6 (docs/household-identity-and-access-design.md): every
pre-existing admin mutation endpoint (world model, event rules, tasks,
scheduler, integrations) now records a durable audit event attributed to
the real actor identity Phase 4 built — passkey session -> real user_id,
break-glass secret -> the "admin" marker. These endpoints had zero
durable audit before this phase; this file is their first API-level test
coverage as well as the audit-attribution regression test the phase's
exit gate calls for.

Mirrors tests/unit/test_control_api_access.py's fixture approach: real
in-memory users.db/cache.db wired via monkeypatched session factories,
exercised through FastAPI's TestClient against the real admin router.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, select

from app.control.api import router as admin_router
from app.models.cache import AuditLog
from app.models.users import Household, User

_SECRET = "unit-test-secret-key-not-a-real-fernet-key"
_AUTH = {"Authorization": f"Bearer {_SECRET}"}


def _threaded_memory_engine() -> object:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


@pytest.fixture
def engines(monkeypatch: pytest.MonkeyPatch) -> tuple[object, object]:
    from app.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("APP_SECRET_KEY", _SECRET)

    users_engine = _threaded_memory_engine()
    cache_engine = _threaded_memory_engine()

    @contextmanager  # type: ignore[misc]
    def _users_session():
        with Session(users_engine) as s:  # type: ignore[arg-type]
            yield s

    @contextmanager  # type: ignore[misc]
    def _cache_session():
        with Session(cache_engine) as s:  # type: ignore[arg-type]
            yield s

    # app.world.repository and app.tasks.repository both import
    # users_session at module level (a pre-existing codebase pattern, not
    # introduced here — see e.g. tests/unit/test_task_state_machine.py
    # doing the same), so patching app.db.users_session alone would not
    # reach them; each consuming module's own bound name needs patching
    # too, or these tests would silently fall through to the real
    # on-disk database on every repository call.
    monkeypatch.setattr("app.db.users_session", _users_session)
    monkeypatch.setattr("app.world.repository.users_session", _users_session)
    monkeypatch.setattr("app.tasks.repository.users_session", _users_session)
    monkeypatch.setattr("app.control.audit.cache_session", _cache_session)
    monkeypatch.setattr("app.integrations.accounts.users_session", _users_session)
    monkeypatch.setattr("app.integrations.oauth_state.cache_session", _cache_session)
    monkeypatch.setattr(
        "app.control.admin_events.emit_admin_event", lambda *a, **k: None, raising=False
    )
    import app.integrations.accounts as accounts_mod

    monkeypatch.setattr(accounts_mod, "_refresh_locks", {})

    with Session(users_engine) as s:  # type: ignore[arg-type]
        s.add(Household(id="hh-1", name="The Home"))
        s.add(User(id="user-1", household_id="hh-1", telegram_id=1, name="Kristian"))
        s.commit()

    yield users_engine, cache_engine
    get_settings.cache_clear()


@pytest.fixture
def client(engines: tuple[object, object]) -> TestClient:
    app = FastAPI(docs_url=None, redoc_url=None)
    app.include_router(admin_router)
    return TestClient(app)


def _last_audit_event(cache_engine: object) -> AuditLog:
    with Session(cache_engine) as s:  # type: ignore[arg-type]
        rows = s.exec(select(AuditLog)).all()
    assert rows, "expected at least one AuditLog row"
    return rows[-1]


# ---------------------------------------------------------------------------
# World model
# ---------------------------------------------------------------------------


def test_upsert_member_is_audited(client: TestClient, engines: tuple[object, object]) -> None:
    resp = client.put("/admin/world-model/member", json={"name": "Alice"}, headers=_AUTH)
    assert resp.status_code == 200

    row = _last_audit_event(engines[1])
    assert row.event_type == "admin.world_model.member_upserted"
    assert row.actor_user_id == "admin"
    assert row.household_id == "hh-1"


def test_upsert_fact_is_audited(client: TestClient, engines: tuple[object, object]) -> None:
    resp = client.put(
        "/admin/world-model/fact",
        json={"scope": "household", "key": "wifi_name", "value": "HomeNet"},
        headers=_AUTH,
    )
    assert resp.status_code == 200

    row = _last_audit_event(engines[1])
    assert row.event_type == "admin.world_model.fact_upserted"


def test_delete_fact_is_audited(client: TestClient, engines: tuple[object, object]) -> None:
    from app.models.world import WorldFact

    users_engine, cache_engine = engines
    with Session(users_engine) as s:  # type: ignore[arg-type]
        fact = WorldFact(household_id="hh-1", scope="household", key="k", value_json='"v"')
        s.add(fact)
        s.commit()
        fact_id = fact.id

    resp = client.delete(f"/admin/world-model/fact/{fact_id}", headers=_AUTH)
    assert resp.status_code == 200
    assert resp.json()["ok"] is True

    row = _last_audit_event(cache_engine)
    assert row.event_type == "admin.world_model.fact_deleted"


def test_upsert_routine_is_audited(client: TestClient, engines: tuple[object, object]) -> None:
    resp = client.put("/admin/world-model/routine", json={"name": "Morning routine"}, headers=_AUTH)
    assert resp.status_code == 200

    row = _last_audit_event(engines[1])
    assert row.event_type == "admin.world_model.routine_upserted"


def test_add_alias_is_audited(client: TestClient, engines: tuple[object, object]) -> None:
    from app.models.world import HouseholdMember

    users_engine, cache_engine = engines
    with Session(users_engine) as s:  # type: ignore[arg-type]
        member = HouseholdMember(household_id="hh-1", name="Alice")
        s.add(member)
        s.commit()
        member_id = member.id

    resp = client.put(
        "/admin/world-model/alias",
        json={"entity_type": "householdmember", "entity_id": member_id, "alias": "Ally"},
        headers=_AUTH,
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True

    row = _last_audit_event(cache_engine)
    assert row.event_type == "admin.world_model.alias_added"


def test_upsert_member_detail_is_audited(
    client: TestClient, engines: tuple[object, object]
) -> None:
    from app.models.world import HouseholdMember

    users_engine, cache_engine = engines
    with Session(users_engine) as s:  # type: ignore[arg-type]
        member = HouseholdMember(household_id="hh-1", name="Alice")
        s.add(member)
        s.commit()
        member_id = member.id

    resp = client.put(
        "/admin/world-model/member-detail",
        json={"detail_type": "interest", "member_id": member_id, "name": "Chess"},
        headers=_AUTH,
    )
    assert resp.status_code == 200

    row = _last_audit_event(cache_engine)
    assert row.event_type == "admin.world_model.member_detail_upserted"


def test_delete_entity_is_audited(client: TestClient, engines: tuple[object, object]) -> None:
    from app.models.world import WorldFact

    users_engine, cache_engine = engines
    with Session(users_engine) as s:  # type: ignore[arg-type]
        fact = WorldFact(household_id="hh-1", scope="household", key="k2", value_json='"v"')
        s.add(fact)
        s.commit()
        fact_id = fact.id

    resp = client.delete(f"/admin/world-model/entity/worldfact/{fact_id}", headers=_AUTH)
    assert resp.status_code == 200
    assert resp.json()["ok"] is True

    row = _last_audit_event(cache_engine)
    assert row.event_type == "admin.world_model.entity_deleted"


def test_review_proposal_is_audited_and_attributes_reviewed_by(
    client: TestClient, engines: tuple[object, object]
) -> None:
    from app.models.world import WorldModelProposal

    users_engine, cache_engine = engines
    with Session(users_engine) as s:  # type: ignore[arg-type]
        proposal = WorldModelProposal(
            household_id="hh-1", proposal_type="fact", payload_json="{}", reason="test"
        )
        s.add(proposal)
        s.commit()
        proposal_id = proposal.id

    resp = client.post(
        f"/admin/world-model/proposals/{proposal_id}/review",
        json={"decision": "accepted"},
        headers=_AUTH,
    )
    assert resp.status_code == 200

    row = _last_audit_event(cache_engine)
    assert row.event_type == "admin.world_model.proposal_reviewed"

    with Session(users_engine) as s:  # type: ignore[arg-type]
        reviewed = s.get(WorldModelProposal, proposal_id)
        assert reviewed is not None
        assert reviewed.reviewed_by == "admin"


def test_bulk_review_is_audited(client: TestClient, engines: tuple[object, object]) -> None:
    from app.models.world import WorldModelProposal

    users_engine, cache_engine = engines
    ids = []
    with Session(users_engine) as s:  # type: ignore[arg-type]
        for i in range(2):
            p = WorldModelProposal(
                household_id="hh-1", proposal_type="fact", payload_json="{}", reason=f"test-{i}"
            )
            s.add(p)
            s.commit()
            ids.append(p.id)

    resp = client.post(
        "/admin/world-model/proposals/bulk",
        json={"proposal_ids": ids, "decision": "rejected"},
        headers=_AUTH,
    )
    assert resp.status_code == 200
    assert resp.json()["reviewed"] == 2

    row = _last_audit_event(cache_engine)
    assert row.event_type == "admin.world_model.proposals_bulk_reviewed"
    assert row.detail
    import json as _json

    assert _json.loads(row.detail)["count"] == 2


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------


def test_task_cancel_is_audited(client: TestClient, engines: tuple[object, object]) -> None:
    from app.models.tasks import Task

    users_engine, cache_engine = engines
    with Session(users_engine) as s:  # type: ignore[arg-type]
        task = Task(household_id="hh-1", user_id="user-1", title="Do a thing")
        s.add(task)
        s.commit()
        task_id = task.id

    resp = client.post(f"/admin/tasks/{task_id}/action", json={"action": "cancel"}, headers=_AUTH)
    assert resp.status_code == 200
    assert resp.json()["status"] == "cancelled"

    row = _last_audit_event(cache_engine)
    assert row.event_type == "admin.task.cancelled"


def test_task_resume_is_audited(client: TestClient, engines: tuple[object, object]) -> None:
    from app.models.tasks import Task

    users_engine, cache_engine = engines
    with Session(users_engine) as s:  # type: ignore[arg-type]
        task = Task(
            household_id="hh-1", user_id="user-1", title="Do a thing", status="AWAITING_INPUT"
        )
        s.add(task)
        s.commit()
        task_id = task.id

    resp = client.post(f"/admin/tasks/{task_id}/action", json={"action": "resume"}, headers=_AUTH)
    assert resp.status_code == 200
    assert resp.json()["status"] == "resumed"

    row = _last_audit_event(cache_engine)
    assert row.event_type == "admin.task.resumed"


# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------


def test_run_now_is_audited(
    client: TestClient, engines: tuple[object, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.models.scheduled_prompts import ScheduledPrompt

    users_engine, cache_engine = engines
    with Session(users_engine) as s:  # type: ignore[arg-type]
        sp = ScheduledPrompt(
            household_id="hh-1",
            user_id="user-1",
            channel_user_id="1",
            name="Morning brief",
            prompt="Say hi",
            recurrence="daily",
            time_of_day="08:00",
        )
        s.add(sp)
        s.commit()
        prompt_id = sp.id

    called = {"count": 0}

    async def _fake_fire(**kwargs: object) -> None:
        called["count"] += 1

    monkeypatch.setattr("app.scheduler.jobs.fire_scheduled_prompt", _fake_fire)

    resp = client.post(f"/admin/scheduler/{prompt_id}/run-now", headers=_AUTH)
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"

    row = _last_audit_event(cache_engine)
    assert row.event_type == "admin.scheduler.run_now"


# ---------------------------------------------------------------------------
# Event rules
# ---------------------------------------------------------------------------


def _rule_body(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "name": "Front door opens",
        "user_id": "user-1",
        "prompt_template": "The front door just opened.",
    }
    body.update(overrides)
    return body


def test_create_event_rule_is_audited(client: TestClient, engines: tuple[object, object]) -> None:
    resp = client.post("/admin/event-rules", json=_rule_body(), headers=_AUTH)
    assert resp.status_code == 200

    row = _last_audit_event(engines[1])
    assert row.event_type == "admin.event_rule.created"


def test_update_event_rule_is_audited(client: TestClient, engines: tuple[object, object]) -> None:
    create_resp = client.post("/admin/event-rules", json=_rule_body(), headers=_AUTH)
    rule_id = create_resp.json()["rule"]["id"]

    resp = client.put(
        f"/admin/event-rules/{rule_id}", json=_rule_body(name="Renamed"), headers=_AUTH
    )
    assert resp.status_code == 200

    row = _last_audit_event(engines[1])
    assert row.event_type == "admin.event_rule.updated"


def test_toggle_event_rule_is_audited(client: TestClient, engines: tuple[object, object]) -> None:
    create_resp = client.post("/admin/event-rules", json=_rule_body(), headers=_AUTH)
    rule_id = create_resp.json()["rule"]["id"]

    resp = client.patch(f"/admin/event-rules/{rule_id}/toggle", headers=_AUTH)
    assert resp.status_code == 200

    row = _last_audit_event(engines[1])
    assert row.event_type == "admin.event_rule.toggled"


def test_delete_event_rule_is_audited(client: TestClient, engines: tuple[object, object]) -> None:
    create_resp = client.post("/admin/event-rules", json=_rule_body(), headers=_AUTH)
    rule_id = create_resp.json()["rule"]["id"]

    resp = client.delete(f"/admin/event-rules/{rule_id}", headers=_AUTH)
    assert resp.status_code == 200
    assert resp.json()["status"] == "deleted"

    row = _last_audit_event(engines[1])
    assert row.event_type == "admin.event_rule.deleted"


def test_test_event_rule_is_audited(
    client: TestClient, engines: tuple[object, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio

    import app.control.event_bus as bus_module

    # Isolate from the real global event bus (matches
    # test_event_bus.py::test_enqueue_drop_on_full's own pattern) — this
    # endpoint really does enqueue onto the process-wide queue, and
    # leaving an item there would desync test_event_bus.py's own
    # identity-based dequeue assertion when the full suite runs together.
    monkeypatch.setattr(bus_module, "_event_bus", asyncio.Queue())

    create_resp = client.post("/admin/event-rules", json=_rule_body(), headers=_AUTH)
    rule_id = create_resp.json()["rule"]["id"]

    resp = client.post(f"/admin/event-rules/{rule_id}/test", headers=_AUTH)
    assert resp.status_code == 200
    assert resp.json()["status"] == "fired"

    row = _last_audit_event(engines[1])
    assert row.event_type == "admin.event_rule.tested"


# ---------------------------------------------------------------------------
# Integrations
# ---------------------------------------------------------------------------


def test_integration_connect_is_audited(
    client: TestClient, engines: tuple[object, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.config import get_settings
    from app.oda.oauth import OAuthServerMetadata

    monkeypatch.setenv("FEATURE_ODA", "true")
    monkeypatch.setenv("ODA_OAUTH_PUBLIC_BASE_URL", "https://home.example.com")
    get_settings.cache_clear()

    metadata = OAuthServerMetadata(
        authorization_endpoint="https://oda.com/o/authorize/",
        token_endpoint="https://oda.com/o/token/",
        revocation_endpoint="https://oda.com/o/revoke_token/",
        registration_endpoint="https://oda.com/o/register/",
    )

    async def _fake_discover() -> OAuthServerMetadata:
        return metadata

    async def _fake_register(metadata: object, redirect_uri: str) -> tuple[str, str]:
        return "client-abc", "client-secret-xyz"

    monkeypatch.setattr("app.oda.oauth.discover_metadata", _fake_discover)
    monkeypatch.setattr("app.oda.oauth.register_client", _fake_register)

    resp = client.post("/admin/integrations/oda/connect", json={"user_id": "user-1"}, headers=_AUTH)
    assert resp.status_code == 200
    assert "authorize_url" in resp.json()

    row = _last_audit_event(engines[1])
    assert row.event_type == "admin.integration.connect_started"
    get_settings.cache_clear()


def test_integration_disconnect_is_audited(
    client: TestClient, engines: tuple[object, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.integrations.accounts import upsert_account

    upsert_account(
        household_id="hh-1",
        provider="oda",
        connected_by_user_id="user-1",
        client_id="client-abc",
        client_secret="",
        access_token="at",
        refresh_token="rt",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )

    from app.oda.oauth import OdaOAuthError

    async def _fake_discover() -> object:
        # Revocation failure must not block the local disconnect — this
        # exercises that best-effort path rather than a real discovery.
        raise OdaOAuthError("discovery unavailable in test")

    monkeypatch.setattr("app.oda.oauth.discover_metadata", _fake_discover)
    monkeypatch.setattr("app.oda.mcp_client.stop_mcp", lambda: _noop_async())
    monkeypatch.setattr("app.agent.agent.reload_agent", lambda: None)

    resp = client.post("/admin/integrations/oda/disconnect", headers=_AUTH)
    assert resp.status_code == 200
    assert resp.json()["disconnected"] is True

    row = _last_audit_event(engines[1])
    assert row.event_type == "admin.integration.disconnected"


async def _noop_async() -> None:
    return None
