"""
World Model Formatter — render a compact markdown snapshot for the agent system prompt.
"""
from __future__ import annotations

import json
import logging

from app.world.repository import WorldModelRepository, WorldModelSnapshot

logger = logging.getLogger(__name__)


def format_world_model(household_id: str, current_user_id: str | None = None) -> str:
    """Return a compact ``## Household Model`` section for the system prompt.

    Returns an empty string if the world model has no data.
    """
    snapshot = WorldModelRepository.get_full_snapshot(household_id)
    if snapshot.is_empty:
        return ""

    sections: list[str] = ["## Household Model"]

    _add_members(sections, snapshot, current_user_id=current_user_id)
    _add_places(sections, snapshot)
    _add_devices(sections, snapshot)
    _add_calendars(sections, snapshot)
    _add_routines(sections, snapshot)
    _add_facts(sections, snapshot)

    return "\n".join(sections)


# ---------------------------------------------------------------------------
# Section builders
# ---------------------------------------------------------------------------


def _add_members(
    sections: list[str],
    snap: WorldModelSnapshot,
    current_user_id: str | None = None,
) -> None:
    if not snap.members:
        return

    # Build per-member interest/goal/activity lookups
    interests_by_member: dict[str, list[str]] = {}
    for i in snap.interests:
        interests_by_member.setdefault(i.member_id, []).append(i.name)

    goals_by_member: dict[str, list[str]] = {}
    for g in snap.goals:
        goals_by_member.setdefault(g.member_id, []).append(g.name)

    activities_by_member: dict[str, list[str]] = {}
    for a in snap.activities:
        label = a.name
        if a.schedule_hint:
            label += f" ({a.schedule_hint})"
        activities_by_member.setdefault(a.member_id, []).append(label)

    sections.append("")
    sections.append("Members:")
    for m in snap.members:
        aliases = _parse_aliases(m.aliases_json)
        alias_str = f" (aka {', '.join(aliases)})" if aliases else ""
        speaking = " \u2190 speaking" if (current_user_id and m.user_id == current_user_id) else ""
        sections.append(f"- {m.name} ({m.role}){alias_str}{speaking}")

        if m.id in interests_by_member:
            sections.append(f"  - interests: {', '.join(interests_by_member[m.id])}")
        if m.id in activities_by_member:
            sections.append(f"  - activities: {', '.join(activities_by_member[m.id])}")
        if m.id in goals_by_member:
            sections.append(f"  - goals: {', '.join(goals_by_member[m.id])}")


def _add_places(sections: list[str], snap: WorldModelSnapshot) -> None:
    if not snap.places:
        return

    # Build hierarchy: parent_id → children
    by_parent: dict[str | None, list] = {}
    place_by_id: dict[str, object] = {}
    for p in snap.places:
        by_parent.setdefault(p.parent_place_id, []).append(p)
        place_by_id[p.id] = p

    sections.append("")
    sections.append("Places:")

    # Render top-level places first, then children inline
    top_level = by_parent.get(None, [])
    for place in top_level:
        children = by_parent.get(place.id, [])
        aliases = _parse_aliases(place.aliases_json)
        alias_str = f" (aka {', '.join(aliases)})" if aliases else ""

        if children:
            child_names = []
            for c in children:
                c_aliases = _parse_aliases(c.aliases_json)
                if c_aliases:
                    child_names.append(f"{c.name} ({', '.join(c_aliases)})")
                else:
                    child_names.append(c.name)
            sections.append(f"- {place.name}{alias_str}: {', '.join(child_names)}")
        else:
            sections.append(f"- {place.name}{alias_str}")

    # Any orphan places not under a top-level parent
    rendered_ids = {p.id for p in top_level}
    for p in top_level:
        for c in by_parent.get(p.id, []):
            rendered_ids.add(c.id)
    for p in snap.places:
        if p.id not in rendered_ids:
            sections.append(f"- {p.name}")


def _add_devices(sections: list[str], snap: WorldModelSnapshot) -> None:
    if not snap.devices:
        return

    # Group devices by place_id
    place_by_id: dict[str, str] = {}
    for p in snap.places:
        place_by_id[p.id] = p.name

    by_place: dict[str | None, list] = {}
    for d in snap.devices:
        by_place.setdefault(d.place_id, []).append(d)

    sections.append("")
    sections.append(f"Devices ({len(snap.devices)}):")

    # Devices grouped by place
    for place_id, devices in by_place.items():
        if place_id and place_id in place_by_id:
            place_name = place_by_id[place_id]
            device_strs = []
            for d in devices:
                aliases = _parse_aliases(d.aliases_json)
                alias_str = f" ({', '.join(aliases)})" if aliases else ""
                type_str = f" [{d.device_type}]" if d.device_type else ""
                device_strs.append(f"{d.name}{alias_str}{type_str}")
            sections.append(f"- {place_name}: {', '.join(device_strs)}")
        else:
            # Devices without a place
            for d in devices:
                aliases = _parse_aliases(d.aliases_json)
                alias_str = f" ({', '.join(aliases)})" if aliases else ""
                type_str = f" [{d.device_type}]" if d.device_type else ""
                sections.append(f"- {d.name}{alias_str}{type_str}")


def _add_calendars(sections: list[str], snap: WorldModelSnapshot) -> None:
    if not snap.calendars:
        return

    member_by_id: dict[str, str] = {m.id: m.name for m in snap.members}

    sections.append("")
    sections.append("Calendars:")
    for c in snap.calendars:
        cat_str = f" [{c.category}]" if c.category and c.category != "general" else ""
        owner = ""
        if c.member_id and c.member_id in member_by_id:
            owner = f" -> {member_by_id[c.member_id]}"
        sections.append(f"- {c.name}{cat_str}{owner}")


def _add_routines(sections: list[str], snap: WorldModelSnapshot) -> None:
    if not snap.routines:
        return

    sections.append("")
    sections.append("Routines:")
    for r in snap.routines:
        desc = f": {r.description}" if r.description else ""
        sections.append(f"- {r.name}{desc}")


def _add_facts(sections: list[str], snap: WorldModelSnapshot) -> None:
    if not snap.facts:
        return

    sections.append("")
    sections.append("Facts:")
    for f in snap.facts:
        try:
            value = json.loads(f.value_json)
        except (json.JSONDecodeError, TypeError):
            value = f.value_json
        # Pretty-print the key
        display_key = f.key.replace("_", " ").replace(".", " / ")
        sections.append(f"- {display_key}: {value}")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_aliases(aliases_json: str) -> list[str]:
    """Parse a JSON alias list, returning empty list on failure."""
    try:
        aliases = json.loads(aliases_json)
        if isinstance(aliases, list):
            return [str(a) for a in aliases if a]
    except (json.JSONDecodeError, TypeError):
        pass
    return []
