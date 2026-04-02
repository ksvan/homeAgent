"""Unit tests for app.world.formatter — pure formatting logic, no DB needed."""
from __future__ import annotations

from unittest.mock import patch

from app.models.world import (
    CalendarEntity,
    DeviceEntity,
    HouseholdMember,
    MemberActivity,
    MemberGoal,
    MemberInterest,
    Place,
    RoutineEntity,
    WorldFact,
)
from app.world.formatter import (
    _add_calendars,
    _add_devices,
    _add_facts,
    _add_members,
    _add_places,
    _add_routines,
    _parse_aliases,
    format_world_model,
)
from app.world.repository import WorldModelSnapshot  # noqa: I001

# ---------------------------------------------------------------------------
# _parse_aliases
# ---------------------------------------------------------------------------


def test_parse_aliases_valid_json() -> None:
    assert _parse_aliases('["a", "b"]') == ["a", "b"]


def test_parse_aliases_empty_list() -> None:
    assert _parse_aliases("[]") == []


def test_parse_aliases_filters_falsy() -> None:
    assert _parse_aliases('["ok", "", null]') == ["ok"]


def test_parse_aliases_bad_json() -> None:
    assert _parse_aliases("not-json") == []


def test_parse_aliases_none() -> None:
    assert _parse_aliases(None) == []  # type: ignore[arg-type]


def test_parse_aliases_non_list_json() -> None:
    assert _parse_aliases('{"a": 1}') == []


# ---------------------------------------------------------------------------
# _add_members
# ---------------------------------------------------------------------------


def _member(
    id: str = "m1",
    name: str = "Alice",
    role: str = "admin",
    aliases: str = "[]",
) -> HouseholdMember:
    return HouseholdMember(
        id=id, household_id="h1", name=name,
        role=role, aliases_json=aliases,
    )


def test_add_members_empty() -> None:
    snap = WorldModelSnapshot()
    sections: list[str] = []
    _add_members(sections, snap)
    assert sections == []


def test_add_members_basic() -> None:
    snap = WorldModelSnapshot(members=[_member()])
    sections: list[str] = []
    _add_members(sections, snap)
    assert "- Alice (admin)" in sections


def test_add_members_with_aliases() -> None:
    snap = WorldModelSnapshot(members=[_member(aliases='["Ali"]')])
    sections: list[str] = []
    _add_members(sections, snap)
    assert any("aka Ali" in s for s in sections)


def test_add_members_with_interests() -> None:
    snap = WorldModelSnapshot(
        members=[_member()],
        interests=[MemberInterest(
            member_id="m1", household_id="h1", name="piano",
        )],
    )
    sections: list[str] = []
    _add_members(sections, snap)
    assert any("interests: piano" in s for s in sections)


def test_add_members_with_activities() -> None:
    snap = WorldModelSnapshot(
        members=[_member()],
        activities=[MemberActivity(
            member_id="m1", household_id="h1",
            name="soccer", schedule_hint="Mon 18:00",
        )],
    )
    sections: list[str] = []
    _add_members(sections, snap)
    assert any("soccer (Mon 18:00)" in s for s in sections)


def test_add_members_with_goals() -> None:
    snap = WorldModelSnapshot(
        members=[_member()],
        goals=[MemberGoal(
            member_id="m1", household_id="h1", name="learn guitar",
        )],
    )
    sections: list[str] = []
    _add_members(sections, snap)
    assert any("goals: learn guitar" in s for s in sections)


# ---------------------------------------------------------------------------
# _add_places
# ---------------------------------------------------------------------------


def _place(
    id: str = "p1",
    name: str = "Living Room",
    parent: str | None = None,
    aliases: str = "[]",
) -> Place:
    return Place(
        id=id, household_id="h1", name=name,
        parent_place_id=parent, aliases_json=aliases,
    )


def test_add_places_empty() -> None:
    snap = WorldModelSnapshot()
    sections: list[str] = []
    _add_places(sections, snap)
    assert sections == []


def test_add_places_flat() -> None:
    snap = WorldModelSnapshot(places=[_place()])
    sections: list[str] = []
    _add_places(sections, snap)
    assert "- Living Room" in sections


def test_add_places_hierarchy() -> None:
    parent = _place(id="p1", name="First Floor")
    child = _place(id="p2", name="Kitchen", parent="p1")
    snap = WorldModelSnapshot(places=[parent, child])
    sections: list[str] = []
    _add_places(sections, snap)
    assert any("First Floor" in s and "Kitchen" in s for s in sections)


def test_add_places_with_aliases() -> None:
    snap = WorldModelSnapshot(places=[_place(aliases='["Stue"]')])
    sections: list[str] = []
    _add_places(sections, snap)
    assert any("aka Stue" in s for s in sections)


# ---------------------------------------------------------------------------
# _add_devices
# ---------------------------------------------------------------------------


def _device(
    id: str = "d1",
    name: str = "Ceiling Light",
    place_id: str | None = None,
    device_type: str = "light",
    aliases: str = "[]",
) -> DeviceEntity:
    return DeviceEntity(
        id=id, household_id="h1", name=name,
        place_id=place_id, device_type=device_type,
        aliases_json=aliases,
    )


def test_add_devices_empty() -> None:
    snap = WorldModelSnapshot()
    sections: list[str] = []
    _add_devices(sections, snap)
    assert sections == []


def test_add_devices_grouped_by_place() -> None:
    place = _place(id="p1", name="Kitchen")
    device = _device(place_id="p1")
    snap = WorldModelSnapshot(places=[place], devices=[device])
    sections: list[str] = []
    _add_devices(sections, snap)
    assert any("Kitchen:" in s and "Ceiling Light" in s for s in sections)


def test_add_devices_without_place() -> None:
    device = _device()
    snap = WorldModelSnapshot(devices=[device])
    sections: list[str] = []
    _add_devices(sections, snap)
    assert any("Ceiling Light" in s and "[light]" in s for s in sections)


def test_add_devices_count_header() -> None:
    devs = [_device(), _device(id="d2", name="Lamp")]
    snap = WorldModelSnapshot(devices=devs)
    sections: list[str] = []
    _add_devices(sections, snap)
    assert any("Devices (2):" in s for s in sections)


# ---------------------------------------------------------------------------
# _add_calendars
# ---------------------------------------------------------------------------


def test_add_calendars_empty() -> None:
    snap = WorldModelSnapshot()
    sections: list[str] = []
    _add_calendars(sections, snap)
    assert sections == []


def test_add_calendars_with_owner() -> None:
    member = _member(id="m1", name="Alice")
    cal = CalendarEntity(
        id="c1", household_id="h1", name="Work",
        member_id="m1", category="work",
    )
    snap = WorldModelSnapshot(members=[member], calendars=[cal])
    sections: list[str] = []
    _add_calendars(sections, snap)
    assert any("Work [work] -> Alice" in s for s in sections)


def test_add_calendars_general_hides_category() -> None:
    cal = CalendarEntity(
        id="c1", household_id="h1", name="Family", category="general",
    )
    snap = WorldModelSnapshot(calendars=[cal])
    sections: list[str] = []
    _add_calendars(sections, snap)
    assert any(s == "- Family" for s in sections)


# ---------------------------------------------------------------------------
# _add_routines
# ---------------------------------------------------------------------------


def test_add_routines_empty() -> None:
    snap = WorldModelSnapshot()
    sections: list[str] = []
    _add_routines(sections, snap)
    assert sections == []


def test_add_routines_with_description() -> None:
    r = RoutineEntity(
        id="r1", household_id="h1",
        name="Night Mode", description="Dim all lights",
    )
    snap = WorldModelSnapshot(routines=[r])
    sections: list[str] = []
    _add_routines(sections, snap)
    assert any("Night Mode: Dim all lights" in s for s in sections)


# ---------------------------------------------------------------------------
# _add_facts
# ---------------------------------------------------------------------------


def test_add_facts_empty() -> None:
    snap = WorldModelSnapshot()
    sections: list[str] = []
    _add_facts(sections, snap)
    assert sections == []


def test_add_facts_json_value() -> None:
    f = WorldFact(
        id="f1", household_id="h1", scope="household",
        key="default_language", value_json='"Norwegian"',
    )
    snap = WorldModelSnapshot(facts=[f])
    sections: list[str] = []
    _add_facts(sections, snap)
    assert any("default language: Norwegian" in s for s in sections)


def test_add_facts_bad_json_fallback() -> None:
    f = WorldFact(
        id="f1", household_id="h1", scope="household",
        key="wifi_ssid", value_json="not-valid",
    )
    snap = WorldModelSnapshot(facts=[f])
    sections: list[str] = []
    _add_facts(sections, snap)
    assert any("wifi ssid: not-valid" in s for s in sections)


def test_add_facts_key_formatting() -> None:
    f = WorldFact(
        id="f1", household_id="h1", scope="device",
        key="night_mode.lights", value_json='"dim"',
    )
    snap = WorldModelSnapshot(facts=[f])
    sections: list[str] = []
    _add_facts(sections, snap)
    assert any("night mode / lights: dim" in s for s in sections)


# ---------------------------------------------------------------------------
# WorldModelSnapshot.is_empty
# ---------------------------------------------------------------------------


def test_snapshot_is_empty_default() -> None:
    assert WorldModelSnapshot().is_empty is True


def test_snapshot_is_empty_with_members() -> None:
    snap = WorldModelSnapshot(members=[_member()])
    assert snap.is_empty is False


def test_snapshot_is_empty_with_only_interests() -> None:
    """Interests alone don't make a snapshot non-empty."""
    snap = WorldModelSnapshot(
        interests=[MemberInterest(
            member_id="m1", household_id="h1", name="x",
        )],
    )
    assert snap.is_empty is True


@patch("app.world.formatter.WorldModelRepository.get_full_snapshot")
def test_format_world_model_includes_usage_hint(mock_snapshot: object) -> None:
    """The formatted output should include a usage-hint line for the LLM."""
    mock_snapshot.return_value = WorldModelSnapshot(members=[_member()])  # type: ignore[union-attr]
    result = format_world_model("h1")
    assert "## Household Model" in result
    assert "resolve references" in result.lower()


def test_snapshot_is_empty_with_facts() -> None:
    snap = WorldModelSnapshot(
        facts=[WorldFact(
            id="f1", household_id="h1",
            scope="h", key="k", value_json='"v"',
        )],
    )
    assert snap.is_empty is False
