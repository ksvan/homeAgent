"""Integration tests for app.world.repository.WorldModelRepository.

Uses an in-memory SQLite engine (via the `in_memory_engine` conftest fixture)
with all world-model tables created.  No mocking — tests run against real SQLite
so they catch query/schema regressions that unit-level mocks would miss.
"""
from __future__ import annotations

from contextlib import contextmanager

import pytest
from sqlmodel import Session

from app.world.repository import WorldModelRepository

HH = "hh-integration-test"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def patch_world_session(monkeypatch: pytest.MonkeyPatch, in_memory_engine: object) -> None:
    """Route all WorldModelRepository DB calls to the per-test in-memory engine."""

    @contextmanager
    def _session():  # type: ignore[misc]
        with Session(in_memory_engine) as s:  # type: ignore[arg-type]
            yield s

    monkeypatch.setattr("app.world.repository.users_session", _session)


repo = WorldModelRepository


# ---------------------------------------------------------------------------
# Snapshot
# ---------------------------------------------------------------------------


def test_empty_household_snapshot_is_empty() -> None:
    snapshot = repo.get_full_snapshot(HH)
    assert snapshot.is_empty is True


def test_snapshot_not_empty_after_member_upsert() -> None:
    repo.upsert_member(HH, name="Alice")
    snapshot = repo.get_full_snapshot(HH)
    assert snapshot.is_empty is False


def test_snapshot_contains_upserted_member() -> None:
    repo.upsert_member(HH, name="Bob", role="admin")
    snapshot = repo.get_full_snapshot(HH)
    names = [m.name for m in snapshot.members]
    assert "Bob" in names


def test_snapshot_contains_upserted_device() -> None:
    repo.upsert_device(HH, name="Living Room Light", device_type="light")
    snapshot = repo.get_full_snapshot(HH)
    names = [d.name for d in snapshot.devices]
    assert "Living Room Light" in names


def test_snapshot_contains_upserted_fact() -> None:
    repo.upsert_world_fact(HH, scope="household", key="language", value="Norwegian")
    snapshot = repo.get_full_snapshot(HH)
    keys = [f.key for f in snapshot.facts]
    assert "language" in keys


# ---------------------------------------------------------------------------
# Member upsert idempotency
# ---------------------------------------------------------------------------


def test_upsert_member_twice_by_name_creates_single_record() -> None:
    repo.upsert_member(HH, name="Charlie")
    repo.upsert_member(HH, name="Charlie", role="admin")
    members = repo.get_members(HH)
    charlie_records = [m for m in members if m.name == "Charlie"]
    assert len(charlie_records) == 1
    assert charlie_records[0].role == "admin"


# ---------------------------------------------------------------------------
# find_member_by_name — case-insensitive and alias lookup
# ---------------------------------------------------------------------------


def test_find_member_by_name_case_insensitive() -> None:
    repo.upsert_member(HH, name="Diana")
    assert repo.find_member_by_name(HH, "diana") is not None
    assert repo.find_member_by_name(HH, "DIANA") is not None


def test_find_member_by_name_not_found_returns_none() -> None:
    assert repo.find_member_by_name(HH, "nosuchperson") is None


def test_find_member_by_alias() -> None:
    member = repo.upsert_member(HH, name="Edward")
    repo.add_alias(HH, "householdmember", member.id, "Ed")
    found = repo.find_member_by_name(HH, "ed")
    assert found is not None
    assert found.id == member.id


# ---------------------------------------------------------------------------
# Alias management
# ---------------------------------------------------------------------------


def test_add_alias_returns_true_on_success() -> None:
    member = repo.upsert_member(HH, name="Fiona")
    result = repo.add_alias(HH, "householdmember", member.id, "Fi")
    assert result is True


def test_add_alias_duplicate_returns_false() -> None:
    member = repo.upsert_member(HH, name="George")
    repo.add_alias(HH, "householdmember", member.id, "G")
    result = repo.add_alias(HH, "householdmember", member.id, "G")
    assert result is False


def test_remove_alias_makes_lookup_fail() -> None:
    member = repo.upsert_member(HH, name="Hannah")
    repo.add_alias(HH, "householdmember", member.id, "Han")
    assert repo.find_member_by_name(HH, "han") is not None

    repo.remove_alias(HH, "householdmember", member.id, "Han")
    assert repo.find_member_by_name(HH, "han") is None


def test_remove_alias_nonexistent_returns_false() -> None:
    member = repo.upsert_member(HH, name="Ivan")
    result = repo.remove_alias(HH, "householdmember", member.id, "no-such-alias")
    assert result is False


def test_add_alias_unknown_entity_type_returns_false() -> None:
    result = repo.add_alias(HH, "unknowntype", "some-id", "x")
    assert result is False


# ---------------------------------------------------------------------------
# WorldFact upsert
# ---------------------------------------------------------------------------


def test_upsert_world_fact_no_overwrite_keeps_original() -> None:
    repo.upsert_world_fact(HH, scope="household", key="pet", value="cat", overwrite=False)
    repo.upsert_world_fact(HH, scope="household", key="pet", value="dog", overwrite=False)
    facts = repo.get_world_facts(HH)
    pet_fact = next(f for f in facts if f.key == "pet")
    import json
    assert json.loads(pet_fact.value_json) == "cat"


def test_upsert_world_fact_overwrite_replaces_value() -> None:
    repo.upsert_world_fact(HH, scope="household", key="city", value="Oslo", overwrite=False)
    repo.upsert_world_fact(HH, scope="household", key="city", value="Bergen", overwrite=True)
    facts = repo.get_world_facts(HH)
    city_fact = next(f for f in facts if f.key == "city")
    import json
    assert json.loads(city_fact.value_json) == "Bergen"


# ---------------------------------------------------------------------------
# delete_entity
# ---------------------------------------------------------------------------


def test_delete_entity_removes_from_snapshot() -> None:
    repo.upsert_world_fact(HH, scope="household", key="to-delete", value="x")
    facts_before = repo.get_world_facts(HH, scope="household")
    fact = next(f for f in facts_before if f.key == "to-delete")

    result = repo.delete_entity("worldfact", fact.id)
    assert result is True

    facts_after = repo.get_world_facts(HH, scope="household")
    assert not any(f.key == "to-delete" for f in facts_after)


def test_delete_entity_nonexistent_returns_false() -> None:
    result = repo.delete_entity("worldfact", "nonexistent-id")
    assert result is False


def test_delete_entity_unknown_type_returns_false() -> None:
    result = repo.delete_entity("householdmember", "some-id")
    assert result is False


# ---------------------------------------------------------------------------
# Place upsert
# ---------------------------------------------------------------------------


def test_upsert_place_idempotent_by_name() -> None:
    repo.upsert_place(HH, name="Kitchen", kind="room")
    repo.upsert_place(HH, name="Kitchen", kind="zone")
    places = repo.get_places(HH)
    kitchen_records = [p for p in places if p.name == "Kitchen"]
    assert len(kitchen_records) == 1
    assert kitchen_records[0].kind == "zone"


def test_upsert_place_by_external_zone_id() -> None:
    repo.upsert_place(HH, name="Living Room", kind="room", external_zone_id="zone-lr")
    repo.upsert_place(HH, name="Living Room Updated", kind="room", external_zone_id="zone-lr")
    places = [p for p in repo.get_places(HH) if p.external_zone_id == "zone-lr"]
    assert len(places) == 1
    assert places[0].name == "Living Room Updated"
