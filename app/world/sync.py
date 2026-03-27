"""
World Model Bootstrap / Sync.

Called on startup to populate the world model from existing trusted sources:
Users, Calendars, Homey zones/devices, and seed facts.

All functions are idempotent — safe to re-run on every startup.
"""
from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)


async def bootstrap_world_model(household_id: str) -> None:
    """Orchestrate all sync steps for a household. Fire-and-forget safe."""
    try:
        _sync_members(household_id)
        _sync_calendars(household_id)
        await _sync_homey(household_id)
        _seed_world_facts(household_id)
        logger.info("World model bootstrap complete for household_id=%s", household_id)
    except Exception:
        logger.error(
            "World model bootstrap failed for household_id=%s", household_id,
            exc_info=True,
        )


# ---------------------------------------------------------------------------
# Step 1: Sync User → HouseholdMember
# ---------------------------------------------------------------------------

def _sync_members(household_id: str) -> None:
    from sqlmodel import select

    from app.db import users_session
    from app.models.users import User
    from app.world.repository import WorldModelRepository

    with users_session() as session:
        users = list(session.exec(
            select(User).where(User.household_id == household_id)
        ).all())

    synced = 0
    for user in users:
        WorldModelRepository.upsert_member(
            household_id,
            user_id=user.id,
            name=user.name,
            role="admin" if user.is_admin else "member",
            source="migration_seed",
        )
        synced += 1

    if synced:
        logger.info("Synced %d user(s) → HouseholdMember", synced)


# ---------------------------------------------------------------------------
# Step 2: Sync Calendar → CalendarEntity
# ---------------------------------------------------------------------------

def _sync_calendars(household_id: str) -> None:
    from sqlmodel import select

    from app.db import users_session
    from app.models.calendars import Calendar
    from app.models.world import HouseholdMember
    from app.world.repository import WorldModelRepository

    with users_session() as session:
        calendars = list(session.exec(
            select(Calendar).where(Calendar.household_id == household_id)
        ).all())
        members = list(session.exec(
            select(HouseholdMember)
            .where(HouseholdMember.household_id == household_id,
                   HouseholdMember.is_active == True)  # noqa: E712
        ).all())

    # Build case-insensitive name→id lookup
    member_lookup: dict[str, str] = {m.name.lower(): m.id for m in members}

    synced = 0
    for cal in calendars:
        member_id = None
        if cal.member_name:
            member_id = member_lookup.get(cal.member_name.lower())

        WorldModelRepository.upsert_calendar_entity(
            household_id,
            calendar_id=cal.id,
            name=cal.name,
            member_id=member_id,
            category=cal.category,
            source="calendar_import",
        )
        synced += 1

    if synced:
        logger.info("Synced %d calendar(s) → CalendarEntity", synced)


# ---------------------------------------------------------------------------
# Step 3+4: Sync Homey zones → Place, Homey devices → DeviceEntity
# ---------------------------------------------------------------------------

async def _sync_homey(household_id: str) -> None:
    """Sync Homey zones and devices into Place and DeviceEntity tables.

    Calls get_home_structure via MCP. Skips gracefully if MCP is unavailable
    or if the response format is not parseable.
    """
    from app.homey.mcp_client import get_mcp_server

    server = get_mcp_server()
    if server is None:
        logger.debug("Homey MCP not available — skipping world model Homey sync")
        return

    from pydantic_ai.mcp import MCPServerStreamableHTTP

    if not isinstance(server, MCPServerStreamableHTTP):
        return

    try:
        result = await server.direct_call_tool("get_home_structure", {}, None)
    except Exception:
        logger.warning("Could not call get_home_structure for world model sync", exc_info=True)
        return

    if not result:
        return

    # The result comes back as a string representation. Try to parse as JSON.
    text = str(result)
    # MCP tool results may be wrapped — try to extract JSON from the string
    data = _try_parse_home_structure(text)
    if data is None:
        logger.info(
            "Could not parse get_home_structure response for world model sync "
            "(length=%d). Homey sync skipped — will retry on next restart.",
            len(text),
        )
        return

    zone_id_map = _sync_zones(household_id, data)
    _sync_devices_from_structure(household_id, data, zone_id_map)


def _try_parse_home_structure(text: str) -> dict | None:
    """Attempt to parse the Homey get_home_structure response.

    Returns a dict on success, None on failure. Handles both direct JSON
    and MCP CallToolResult wrapping.
    """
    # Try direct JSON parse first
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except (json.JSONDecodeError, TypeError):
        pass

    # Try to find a JSON object in the string (common with MCP string wrapping)
    brace_start = text.find("{")
    if brace_start >= 0:
        try:
            parsed = json.loads(text[brace_start:])
            if isinstance(parsed, dict):
                return parsed
        except (json.JSONDecodeError, TypeError):
            pass

    return None


def _sync_zones(household_id: str, data: dict) -> dict[str, str]:
    """Sync zones from Homey structure into Place table.

    Returns a mapping of external_zone_id → Place.id for device linking.
    """
    from app.world.repository import WorldModelRepository

    zones = data.get("zones") or data.get("Zones") or {}
    if isinstance(zones, list):
        zones = {z.get("id", ""): z for z in zones if isinstance(z, dict)}

    zone_id_map: dict[str, str] = {}  # homey_zone_id → Place.id
    synced = 0

    # First pass: create all zones without parent links
    for zone_id, zone_info in zones.items():
        if not isinstance(zone_info, dict):
            continue
        name = zone_info.get("name", str(zone_id))

        # Infer kind from zone properties
        kind = "room"
        parent_homey = zone_info.get("parent")
        if parent_homey is None or parent_homey == "":
            kind = "zone"  # top-level zones are the household root

        place = WorldModelRepository.upsert_place(
            household_id,
            name=name,
            kind=kind,
            external_zone_id=str(zone_id),
            source="homey_import",
        )
        zone_id_map[str(zone_id)] = place.id
        synced += 1

    # Second pass: wire up parent relationships
    for zone_id, zone_info in zones.items():
        if not isinstance(zone_info, dict):
            continue
        parent_homey = zone_info.get("parent")
        if parent_homey and str(parent_homey) in zone_id_map:
            place_id = zone_id_map[str(zone_id)]
            parent_place_id = zone_id_map[str(parent_homey)]
            from datetime import datetime, timezone

            from app.db import users_session
            from app.models.world import Place

            with users_session() as session:
                place = session.get(Place, place_id)
                if place and place.parent_place_id != parent_place_id:
                    place.parent_place_id = parent_place_id
                    place.updated_at = datetime.now(timezone.utc)
                    session.add(place)
                    session.commit()

    if synced:
        logger.info("Synced %d Homey zone(s) → Place", synced)

    return zone_id_map


def _sync_devices_from_structure(
    household_id: str, data: dict, zone_id_map: dict[str, str]
) -> None:
    """Sync devices from Homey structure into DeviceEntity table."""
    from app.world.repository import WorldModelRepository

    devices = data.get("devices") or data.get("Devices") or {}
    if isinstance(devices, list):
        devices = {d.get("id", ""): d for d in devices if isinstance(d, dict)}

    synced = 0
    for device_id, dev_info in devices.items():
        if not isinstance(dev_info, dict):
            continue

        name = dev_info.get("name", str(device_id))
        device_type = dev_info.get("class", dev_info.get("type", ""))

        # Determine place from zone
        zone_id = dev_info.get("zone") or dev_info.get("zoneId")
        place_id = zone_id_map.get(str(zone_id)) if zone_id else None

        # Extract capabilities
        caps = dev_info.get("capabilities", [])
        if isinstance(caps, dict):
            caps = list(caps.keys())
        caps_json = json.dumps(caps) if caps else "[]"

        # Infer controllability
        is_controllable = device_type not in ("sensor", "homealarm")

        WorldModelRepository.upsert_device(
            household_id,
            name=name,
            external_device_id=str(device_id),
            device_type=str(device_type),
            place_id=place_id,
            capabilities_json=caps_json,
            is_controllable=is_controllable,
            source="homey_import",
        )
        synced += 1

    if synced:
        logger.info("Synced %d Homey device(s) → DeviceEntity", synced)


# ---------------------------------------------------------------------------
# Step 5: Seed world facts
# ---------------------------------------------------------------------------

def _seed_world_facts(household_id: str) -> None:
    """Seed hardcoded world facts. Uses insert-or-skip semantics."""
    from app.world.repository import WorldModelRepository

    seeds: list[tuple[str, str, object]] = [
        # (scope, key, value)
        ("routine", "night_mode.lights", "off"),
        ("routine", "night_mode.heating", "unchanged"),
        ("household", "default_language", "no"),
        ("household", "wake_hours_weekday", "06:30-23:00"),
    ]

    seeded = 0
    for scope, key, value in seeds:
        fact = WorldModelRepository.upsert_world_fact(
            household_id,
            scope=scope,
            key=key,
            value=value,
            source="migration_seed",
            overwrite=False,
        )
        if fact:
            seeded += 1

    if seeded:
        logger.info("Seeded %d world fact(s)", seeded)
