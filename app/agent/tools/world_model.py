from __future__ import annotations

import json
import logging

from pydantic_ai import Agent, RunContext

from app.agent.agent import AgentDeps

logger = logging.getLogger(__name__)


def register_world_model_tools(agent: Agent[AgentDeps, str]) -> None:
    """Attach world-model read/write tools to the conversation agent."""

    @agent.tool
    async def update_world_model(
        ctx: RunContext[AgentDeps],
        update_type: str,
        details: str,
    ) -> str:
        """Update the household world model with a structured fact.

        This is the PREFERRED tool for storing structured household knowledge.
        Always use this instead of store_memory when the fact is about:
          - A device's purpose or meaning ("the Tibber Pulse shows total power consumption")
          - A place alias ("kontor means the upstairs office")
          - A member's interest, activity, or goal ("Sondre plays football")
          - A routine definition ("night mode means lights off, heating unchanged")
          - Any household fact that maps to a key-value pair

        Call when a user explicitly states, corrects, or defines a household fact.
        Do NOT call for casual mentions, temporary situations, or information
        that is already in the world model.

        Args:
            update_type: The kind of update. One of:
                - "member"   — add or update a household member (children, guests, etc.)
                - "fact"     — a household/device/routine fact (key-value)
                - "alias"    — an alternative name for a place, device, or member
                - "interest" — a household member's interest or hobby
                - "activity" — a recurring activity with optional schedule
                - "goal"     — a member's active goal
                - "routine"  — a household routine or mode definition
            details: A JSON string with type-specific fields:
                member:   {"name": "...", "role": "member|child|guest"}
                fact:     {"scope": "device|routine|household|member", "key": "...", "value": "..."}
                alias:    {"entity_type": "place|device|member", "entity_name": "...", "alias": "..."}
                interest: {"member_name": "...", "name": "...", "notes": ""}
                activity: {"member_name": "...", "name": "...", "schedule": "", "notes": ""}
                goal:     {"member_name": "...", "name": "...", "notes": ""}
                routine:  {"name": "...", "description": "...", "kind": "mode|schedule|procedure"}
        """
        from app.config import get_settings

        if not get_settings().features.world_model_tools:
            return "World model tools are currently disabled."

        household_id = ctx.deps.household_id
        if not household_id:
            return "No household context available."

        try:
            d = json.loads(details)
        except (json.JSONDecodeError, TypeError):
            return "Invalid JSON in details parameter."

        from app.control.events import emit
        from app.world.repository import WorldModelRepository as repo

        update_type = update_type.lower().strip()

        if update_type == "member":
            name = d.get("name", "")
            if not name:
                return "Missing 'name' in member details."
            role = d.get("role", "member")
            if role not in ("member", "child", "guest", "admin"):
                return f"Invalid role '{role}'. Use: member, child, guest."
            member = repo.upsert_member(
                household_id, name=name, role=role,
                source="user_explicit",
            )
            emit("world.update", {"entity_type": "member", "action": "upsert", "name": member.name})
            logger.info("World model member upserted: %s (%s)", member.name, role)
            return f"Stored member '{member.name}' with role '{role}'."

        if update_type == "fact":
            scope = d.get("scope", "household")
            key = d.get("key", "")
            value = d.get("value", "")
            if not key:
                return "Missing 'key' in fact details."
            repo.upsert_world_fact(
                household_id, scope=scope, key=key, value=value,
                source="user_explicit", overwrite=True,
            )
            emit("world.update", {"entity_type": "fact", "action": "upsert", "key": key})
            logger.info("World model fact upserted: %s.%s", scope, key)
            return f"Stored world fact: {scope}.{key} = {value}"

        if update_type == "alias":
            etype = d.get("entity_type", "").lower()
            ename = d.get("entity_name", "")
            alias = d.get("alias", "")
            if not ename or not alias:
                return "Missing 'entity_name' or 'alias' in details."

            # Map friendly names to internal table names
            type_map = {"place": "place", "device": "deviceentity", "member": "householdmember"}
            internal_type = type_map.get(etype)
            if not internal_type:
                return f"Unknown entity type '{etype}'. Use 'place', 'device', or 'member'."

            # Find entity by name
            finders = {
                "place": repo.find_place_by_name,
                "deviceentity": repo.find_device_by_name,
                "householdmember": repo.find_member_by_name,
            }
            entity = finders[internal_type](household_id, ename)
            if entity is None:
                return f"Could not find {etype} named '{ename}'."

            ok = repo.add_alias(household_id, internal_type, entity.id, alias)
            if not ok:
                return f"Alias '{alias}' already exists for {entity.name}."
            emit("world.update", {"entity_type": etype, "action": "alias_added", "name": entity.name, "alias": alias})
            logger.info("World model alias added: %s '%s' -> '%s'", etype, entity.name, alias)
            return f"Added alias '{alias}' for {etype} '{entity.name}'."

        if update_type == "interest":
            member_name = d.get("member_name", "")
            name = d.get("name", "")
            if not member_name or not name:
                return "Missing 'member_name' or 'name' in details."
            member = repo.find_member_by_name(household_id, member_name)
            if member is None:
                return f"Could not find member named '{member_name}'."
            repo.upsert_interest(
                household_id, member_id=member.id, name=name,
                notes=d.get("notes", ""), source="user_explicit",
            )
            emit("world.update", {"entity_type": "interest", "action": "upsert", "member": member.name, "name": name})
            logger.info("World model interest upserted: %s -> %s", member.name, name)
            return f"Stored interest '{name}' for {member.name}."

        if update_type == "activity":
            member_name = d.get("member_name", "")
            name = d.get("name", "")
            if not member_name or not name:
                return "Missing 'member_name' or 'name' in details."
            member = repo.find_member_by_name(household_id, member_name)
            if member is None:
                return f"Could not find member named '{member_name}'."
            repo.upsert_activity(
                household_id, member_id=member.id, name=name,
                schedule_hint=d.get("schedule", ""), notes=d.get("notes", ""),
                source="user_explicit",
            )
            emit("world.update", {"entity_type": "activity", "action": "upsert", "member": member.name, "name": name})
            logger.info("World model activity upserted: %s -> %s", member.name, name)
            return f"Stored activity '{name}' for {member.name}."

        if update_type == "goal":
            member_name = d.get("member_name", "")
            name = d.get("name", "")
            if not member_name or not name:
                return "Missing 'member_name' or 'name' in details."
            member = repo.find_member_by_name(household_id, member_name)
            if member is None:
                return f"Could not find member named '{member_name}'."
            repo.upsert_goal(
                household_id, member_id=member.id, name=name,
                notes=d.get("notes", ""), source="user_explicit",
            )
            emit("world.update", {"entity_type": "goal", "action": "upsert", "member": member.name, "name": name})
            logger.info("World model goal upserted: %s -> %s", member.name, name)
            return f"Stored goal '{name}' for {member.name}."

        if update_type == "routine":
            name = d.get("name", "")
            if not name:
                return "Missing 'name' in routine details."
            repo.upsert_routine(
                household_id, name=name,
                description=d.get("description", ""),
                kind=d.get("kind", ""),
                source="user_explicit",
            )
            emit("world.update", {"entity_type": "routine", "action": "upsert", "name": name})
            logger.info("World model routine upserted: %s", name)
            return f"Stored routine '{name}'."

        return f"Unknown update_type '{update_type}'. Use: member, fact, alias, interest, activity, goal, routine."

    @agent.tool
    async def remove_world_model_entry(
        ctx: RunContext[AgentDeps],
        entry_type: str,
        identifier: str,
    ) -> str:
        """Remove an entry from the household world model.

        Use when a user asks to forget or remove a previously stored fact, interest,
        activity, goal, or alias.

        Only removes leaf data — does NOT delete core entities like members, places,
        or devices.

        Args:
            entry_type: One of "fact", "alias", "interest", "activity", "goal".
            identifier: A JSON string identifying what to remove:
                fact:     {"scope": "...", "key": "..."}
                alias:    {"entity_type": "place|device|member", "entity_name": "...", "alias": "..."}
                interest: {"member_name": "...", "name": "..."}
                activity: {"member_name": "...", "name": "..."}
                goal:     {"member_name": "...", "name": "..."}
        """
        from app.config import get_settings

        if not get_settings().features.world_model_tools:
            return "World model tools are currently disabled."

        household_id = ctx.deps.household_id
        if not household_id:
            return "No household context available."

        try:
            d = json.loads(identifier)
        except (json.JSONDecodeError, TypeError):
            return "Invalid JSON in identifier parameter."

        from app.control.events import emit
        from app.world.repository import WorldModelRepository as repo

        entry_type = entry_type.lower().strip()

        if entry_type == "fact":
            scope = d.get("scope", "")
            key = d.get("key", "")
            if not scope or not key:
                return "Missing 'scope' or 'key'."
            ok = repo.delete_fact(household_id, scope, key)
            if ok:
                emit("world.update", {"entity_type": "fact", "action": "delete", "key": key})
                return f"Removed fact {scope}.{key}."
            return f"Fact {scope}.{key} not found."

        if entry_type == "alias":
            etype = d.get("entity_type", "").lower()
            ename = d.get("entity_name", "")
            alias = d.get("alias", "")
            type_map = {"place": "place", "device": "deviceentity", "member": "householdmember"}
            internal_type = type_map.get(etype)
            if not internal_type or not ename or not alias:
                return "Missing entity_type, entity_name, or alias."
            finders = {
                "place": repo.find_place_by_name,
                "deviceentity": repo.find_device_by_name,
                "householdmember": repo.find_member_by_name,
            }
            entity = finders[internal_type](household_id, ename)
            if entity is None:
                return f"Could not find {etype} named '{ename}'."
            ok = repo.remove_alias(household_id, internal_type, entity.id, alias)
            if ok:
                emit("world.update", {"entity_type": etype, "action": "alias_removed", "name": entity.name, "alias": alias})
                return f"Removed alias '{alias}' from {etype} '{entity.name}'."
            return f"Alias '{alias}' not found on {entity.name}."

        if entry_type in ("interest", "activity", "goal"):
            member_name = d.get("member_name", "")
            name = d.get("name", "")
            if not member_name or not name:
                return "Missing 'member_name' or 'name'."
            member = repo.find_member_by_name(household_id, member_name)
            if member is None:
                return f"Could not find member named '{member_name}'."
            deleter = {
                "interest": repo.delete_interest,
                "activity": repo.delete_activity,
                "goal": repo.delete_goal,
            }[entry_type]
            ok = deleter(member.id, name)
            if ok:
                emit("world.update", {"entity_type": entry_type, "action": "delete", "member": member.name, "name": name})
                return f"Removed {entry_type} '{name}' from {member.name}."
            return f"{entry_type.title()} '{name}' not found for {member.name}."

        return f"Unknown entry_type '{entry_type}'. Use: fact, alias, interest, activity, goal."

    @agent.tool
    async def list_world_entities(
        ctx: RunContext[AgentDeps],
        entity_type: str = "all",
    ) -> str:
        """List entities in the household world model.

        Use when you need more detail than the compact world model in your context,
        or when the user asks to see what's stored about the household.

        Args:
            entity_type: What to list. One of:
                "all"      — summary counts of all entity types
                "members"  — household members with interests/activities/goals
                "places"   — places with aliases
                "devices"  — devices with types and locations
                "routines" — routines with descriptions
                "facts"    — all world facts
                "calendars"— calendars with member links
        """
        from app.config import get_settings

        if not get_settings().features.world_model_tools:
            return "World model tools are currently disabled."

        household_id = ctx.deps.household_id
        if not household_id:
            return "No household context available."

        from app.world.repository import WorldModelRepository as repo

        entity_type = entity_type.lower().strip()

        if entity_type == "all":
            snap = repo.get_full_snapshot(household_id)
            parts = [
                f"Members: {len(snap.members)}",
                f"Places: {len(snap.places)}",
                f"Devices: {len(snap.devices)}",
                f"Calendars: {len(snap.calendars)}",
                f"Routines: {len(snap.routines)}",
                f"Facts: {len(snap.facts)}",
                f"Relationships: {len(snap.relationships)}",
                f"Interests: {len(snap.interests)}",
                f"Activities: {len(snap.activities)}",
                f"Goals: {len(snap.goals)}",
            ]
            return "World model summary:\n" + "\n".join(parts)

        if entity_type == "members":
            snap = repo.get_full_snapshot(household_id)
            lines: list[str] = []
            for m in snap.members:
                aliases = json.loads(m.aliases_json) if m.aliases_json != "[]" else []
                alias_str = f" (aka {', '.join(aliases)})" if aliases else ""
                lines.append(f"- {m.name} [{m.role}]{alias_str}")
                for i in snap.interests:
                    if i.member_id == m.id:
                        lines.append(f"  interest: {i.name}")
                for a in snap.activities:
                    if a.member_id == m.id:
                        sched = f" ({a.schedule_hint})" if a.schedule_hint else ""
                        lines.append(f"  activity: {a.name}{sched}")
                for g in snap.goals:
                    if g.member_id == m.id:
                        lines.append(f"  goal: {g.name} [{g.status}]")
            return "Members:\n" + "\n".join(lines) if lines else "No members."

        if entity_type == "places":
            places = repo.get_places(household_id)
            lines = []
            for p in places:
                aliases = json.loads(p.aliases_json) if p.aliases_json != "[]" else []
                alias_str = f" (aka {', '.join(aliases)})" if aliases else ""
                parent = ""
                if p.parent_place_id:
                    pp = next((x for x in places if x.id == p.parent_place_id), None)
                    parent = f" -> {pp.name}" if pp else ""
                lines.append(f"- {p.name} [{p.kind}]{alias_str}{parent}")
            return "Places:\n" + "\n".join(lines) if lines else "No places."

        if entity_type == "devices":
            snap = repo.get_full_snapshot(household_id)
            lines = []
            for d in snap.devices:
                place = next((p for p in snap.places if p.id == d.place_id), None)
                loc = f" -> {place.name}" if place else ""
                dtype = f" [{d.device_type}]" if d.device_type else ""
                lines.append(f"- {d.name}{dtype}{loc}")
            return f"Devices ({len(lines)}):\n" + "\n".join(lines) if lines else "No devices."

        if entity_type == "routines":
            routines = repo.get_routines(household_id)
            lines = []
            for r in routines:
                desc = f": {r.description}" if r.description else ""
                kind = f" [{r.kind}]" if r.kind else ""
                lines.append(f"- {r.name}{kind}{desc}")
            return "Routines:\n" + "\n".join(lines) if lines else "No routines."

        if entity_type == "facts":
            facts = repo.get_world_facts(household_id)
            lines = []
            for f in facts:
                try:
                    val = json.loads(f.value_json)
                except (json.JSONDecodeError, TypeError):
                    val = f.value_json
                lines.append(f"- [{f.scope}] {f.key} = {val}")
            return "Facts:\n" + "\n".join(lines) if lines else "No facts."

        if entity_type == "calendars":
            snap = repo.get_full_snapshot(household_id)
            lines = []
            for c in snap.calendars:
                member = next((m for m in snap.members if m.id == c.member_id), None)
                owner = f" -> {member.name}" if member else ""
                cat = f" [{c.category}]" if c.category != "general" else ""
                lines.append(f"- {c.name}{cat}{owner}")
            return "Calendars:\n" + "\n".join(lines) if lines else "No calendars."

        return f"Unknown entity_type '{entity_type}'. Use: all, members, places, devices, routines, facts, calendars."
