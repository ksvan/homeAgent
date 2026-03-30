"""
World Model Repository — clean DB access layer for all world-model queries.

All methods use users_session() internally. A future Postgres migration only
needs to swap the session factory.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlmodel import select

from app.db import users_session
from app.models.world import (
    CalendarEntity,
    DeviceEntity,
    HouseholdMember,
    MemberActivity,
    MemberGoal,
    MemberInterest,
    Place,
    Relationship,
    RoutineEntity,
    WorldFact,
    WorldModelProposal,
)

logger = logging.getLogger(__name__)


@dataclass
class WorldModelSnapshot:
    """In-memory snapshot of the full world model for a household."""

    members: list[HouseholdMember] = field(default_factory=list)
    interests: list[MemberInterest] = field(default_factory=list)
    goals: list[MemberGoal] = field(default_factory=list)
    activities: list[MemberActivity] = field(default_factory=list)
    places: list[Place] = field(default_factory=list)
    devices: list[DeviceEntity] = field(default_factory=list)
    calendars: list[CalendarEntity] = field(default_factory=list)
    routines: list[RoutineEntity] = field(default_factory=list)
    relationships: list[Relationship] = field(default_factory=list)
    facts: list[WorldFact] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not any([
            self.members, self.places, self.devices, self.calendars,
            self.routines, self.facts,
        ])


class WorldModelRepository:
    """Static methods for reading and writing world-model entities."""

    # ------------------------------------------------------------------
    # Read methods
    # ------------------------------------------------------------------

    @staticmethod
    def get_full_snapshot(household_id: str) -> WorldModelSnapshot:
        """Load the entire world model for a household in a single session."""
        with users_session() as session:
            members = list(session.exec(
                select(HouseholdMember)
                .where(HouseholdMember.household_id == household_id,
                       HouseholdMember.is_active == True)  # noqa: E712
            ).all())

            member_ids = [m.id for m in members]

            interests = list(session.exec(
                select(MemberInterest)
                .where(MemberInterest.household_id == household_id)
            ).all()) if member_ids else []

            goals = list(session.exec(
                select(MemberGoal)
                .where(MemberGoal.household_id == household_id,
                       MemberGoal.status == "active")
            ).all()) if member_ids else []

            activities = list(session.exec(
                select(MemberActivity)
                .where(MemberActivity.household_id == household_id)
            ).all()) if member_ids else []

            places = list(session.exec(
                select(Place)
                .where(Place.household_id == household_id)
            ).all())

            devices = list(session.exec(
                select(DeviceEntity)
                .where(DeviceEntity.household_id == household_id)
            ).all())

            calendars = list(session.exec(
                select(CalendarEntity)
                .where(CalendarEntity.household_id == household_id,
                       CalendarEntity.is_active == True)  # noqa: E712
            ).all())

            routines = list(session.exec(
                select(RoutineEntity)
                .where(RoutineEntity.household_id == household_id)
            ).all())

            relationships = list(session.exec(
                select(Relationship)
                .where(Relationship.household_id == household_id)
            ).all())

            facts = list(session.exec(
                select(WorldFact)
                .where(WorldFact.household_id == household_id)
            ).all())

        return WorldModelSnapshot(
            members=members,
            interests=interests,
            goals=goals,
            activities=activities,
            places=places,
            devices=devices,
            calendars=calendars,
            routines=routines,
            relationships=relationships,
            facts=facts,
        )

    @staticmethod
    def get_members(household_id: str) -> list[HouseholdMember]:
        with users_session() as session:
            return list(session.exec(
                select(HouseholdMember)
                .where(HouseholdMember.household_id == household_id,
                       HouseholdMember.is_active == True)  # noqa: E712
            ).all())

    @staticmethod
    def get_places(household_id: str) -> list[Place]:
        with users_session() as session:
            return list(session.exec(
                select(Place).where(Place.household_id == household_id)
            ).all())

    @staticmethod
    def get_devices(household_id: str) -> list[DeviceEntity]:
        with users_session() as session:
            return list(session.exec(
                select(DeviceEntity).where(DeviceEntity.household_id == household_id)
            ).all())

    @staticmethod
    def get_calendars(household_id: str) -> list[CalendarEntity]:
        with users_session() as session:
            return list(session.exec(
                select(CalendarEntity)
                .where(CalendarEntity.household_id == household_id,
                       CalendarEntity.is_active == True)  # noqa: E712
            ).all())

    @staticmethod
    def get_routines(household_id: str) -> list[RoutineEntity]:
        with users_session() as session:
            return list(session.exec(
                select(RoutineEntity).where(RoutineEntity.household_id == household_id)
            ).all())

    @staticmethod
    def get_relationships(
        household_id: str,
        subject_type: str | None = None,
        subject_id: str | None = None,
    ) -> list[Relationship]:
        with users_session() as session:
            stmt = select(Relationship).where(Relationship.household_id == household_id)
            if subject_type:
                stmt = stmt.where(Relationship.subject_type == subject_type)
            if subject_id:
                stmt = stmt.where(Relationship.subject_id == subject_id)
            return list(session.exec(stmt).all())

    @staticmethod
    def get_world_facts(household_id: str, scope: str | None = None) -> list[WorldFact]:
        with users_session() as session:
            stmt = select(WorldFact).where(WorldFact.household_id == household_id)
            if scope:
                stmt = stmt.where(WorldFact.scope == scope)
            return list(session.exec(stmt).all())

    @staticmethod
    def get_member_interests(member_id: str) -> list[MemberInterest]:
        with users_session() as session:
            return list(session.exec(
                select(MemberInterest).where(MemberInterest.member_id == member_id)
            ).all())

    @staticmethod
    def get_member_goals(member_id: str) -> list[MemberGoal]:
        with users_session() as session:
            return list(session.exec(
                select(MemberGoal)
                .where(MemberGoal.member_id == member_id, MemberGoal.status == "active")
            ).all())

    @staticmethod
    def get_member_activities(member_id: str) -> list[MemberActivity]:
        with users_session() as session:
            return list(session.exec(
                select(MemberActivity).where(MemberActivity.member_id == member_id)
            ).all())

    # ------------------------------------------------------------------
    # Upsert methods (used by sync)
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Name-based lookup (used by agent tools to resolve natural language)
    # ------------------------------------------------------------------

    @staticmethod
    def find_member_by_name(household_id: str, name: str) -> HouseholdMember | None:
        """Case-insensitive lookup by name or alias."""
        with users_session() as session:
            members = session.exec(
                select(HouseholdMember)
                .where(HouseholdMember.household_id == household_id,
                       HouseholdMember.is_active == True)  # noqa: E712
            ).all()
            needle = name.lower()
            for m in members:
                if m.name.lower() == needle:
                    return m
                try:
                    aliases = json.loads(m.aliases_json) if m.aliases_json else []
                except (json.JSONDecodeError, TypeError):
                    aliases = []
                if any(a.lower() == needle for a in aliases):
                    return m
        return None

    @staticmethod
    def find_place_by_name(household_id: str, name: str) -> Place | None:
        """Case-insensitive lookup by name or alias."""
        with users_session() as session:
            places = session.exec(
                select(Place).where(Place.household_id == household_id)
            ).all()
            needle = name.lower()
            for p in places:
                if p.name.lower() == needle:
                    return p
                try:
                    aliases = json.loads(p.aliases_json) if p.aliases_json else []
                except (json.JSONDecodeError, TypeError):
                    aliases = []
                if any(a.lower() == needle for a in aliases):
                    return p
        return None

    @staticmethod
    def find_device_by_name(household_id: str, name: str) -> DeviceEntity | None:
        """Case-insensitive lookup by name or alias."""
        with users_session() as session:
            devices = session.exec(
                select(DeviceEntity).where(DeviceEntity.household_id == household_id)
            ).all()
            needle = name.lower()
            for d in devices:
                if d.name.lower() == needle:
                    return d
                try:
                    aliases = json.loads(d.aliases_json) if d.aliases_json else []
                except (json.JSONDecodeError, TypeError):
                    aliases = []
                if any(a.lower() == needle for a in aliases):
                    return d
        return None

    @staticmethod
    def find_routine_by_name(household_id: str, name: str) -> RoutineEntity | None:
        """Case-insensitive lookup by name."""
        with users_session() as session:
            routines = session.exec(
                select(RoutineEntity).where(RoutineEntity.household_id == household_id)
            ).all()
            needle = name.lower()
            for r in routines:
                if r.name.lower() == needle:
                    return r
        return None

    # ------------------------------------------------------------------
    # Upsert methods (used by sync and agent tools)
    # ------------------------------------------------------------------

    @staticmethod
    def upsert_member(
        household_id: str,
        *,
        user_id: str | None = None,
        name: str,
        role: str = "member",
        source: str = "migration_seed",
    ) -> HouseholdMember:
        """Upsert a HouseholdMember by user_id (if set) or name."""
        now = datetime.now(timezone.utc)
        with users_session() as session:
            existing = None
            if user_id:
                existing = session.exec(
                    select(HouseholdMember)
                    .where(HouseholdMember.household_id == household_id,
                           HouseholdMember.user_id == user_id)
                ).first()
            if existing is None:
                existing = session.exec(
                    select(HouseholdMember)
                    .where(HouseholdMember.household_id == household_id,
                           HouseholdMember.name == name)
                ).first()

            if existing:
                existing.name = name
                existing.role = role
                if user_id:
                    existing.user_id = user_id
                existing.updated_at = now
                session.add(existing)
                session.commit()
                session.refresh(existing)
                return existing

            member = HouseholdMember(
                household_id=household_id,
                user_id=user_id,
                name=name,
                role=role,
                source=source,
            )
            session.add(member)
            session.commit()
            session.refresh(member)
            return member

    @staticmethod
    def upsert_place(
        household_id: str,
        *,
        name: str,
        kind: str = "room",
        parent_place_id: str | None = None,
        external_zone_id: str | None = None,
        source: str = "migration_seed",
    ) -> Place:
        """Upsert a Place by external_zone_id (if set) or name."""
        now = datetime.now(timezone.utc)
        with users_session() as session:
            existing = None
            if external_zone_id:
                existing = session.exec(
                    select(Place)
                    .where(Place.household_id == household_id,
                           Place.external_zone_id == external_zone_id)
                ).first()
            if existing is None:
                existing = session.exec(
                    select(Place)
                    .where(Place.household_id == household_id,
                           Place.name == name)
                ).first()

            if existing:
                existing.name = name
                existing.kind = kind
                if parent_place_id is not None:
                    existing.parent_place_id = parent_place_id
                if external_zone_id:
                    existing.external_zone_id = external_zone_id
                existing.updated_at = now
                session.add(existing)
                session.commit()
                session.refresh(existing)
                return existing

            place = Place(
                household_id=household_id,
                name=name,
                kind=kind,
                parent_place_id=parent_place_id,
                external_zone_id=external_zone_id,
                source=source,
            )
            session.add(place)
            session.commit()
            session.refresh(place)
            return place

    @staticmethod
    def upsert_device(
        household_id: str,
        *,
        name: str,
        external_device_id: str | None = None,
        device_type: str = "",
        place_id: str | None = None,
        capabilities_json: str = "[]",
        is_controllable: bool = True,
        source: str = "migration_seed",
    ) -> DeviceEntity:
        """Upsert a DeviceEntity by external_device_id (if set) or name."""
        now = datetime.now(timezone.utc)
        with users_session() as session:
            existing = None
            if external_device_id:
                existing = session.exec(
                    select(DeviceEntity)
                    .where(DeviceEntity.household_id == household_id,
                           DeviceEntity.external_device_id == external_device_id)
                ).first()
            if existing is None:
                existing = session.exec(
                    select(DeviceEntity)
                    .where(DeviceEntity.household_id == household_id,
                           DeviceEntity.name == name)
                ).first()

            if existing:
                existing.name = name
                existing.device_type = device_type
                if place_id is not None:
                    existing.place_id = place_id
                if external_device_id:
                    existing.external_device_id = external_device_id
                existing.capabilities_json = capabilities_json
                existing.is_controllable = is_controllable
                existing.updated_at = now
                session.add(existing)
                session.commit()
                session.refresh(existing)
                return existing

            device = DeviceEntity(
                household_id=household_id,
                name=name,
                external_device_id=external_device_id,
                device_type=device_type,
                place_id=place_id,
                capabilities_json=capabilities_json,
                is_controllable=is_controllable,
                source=source,
            )
            session.add(device)
            session.commit()
            session.refresh(device)
            return device

    @staticmethod
    def upsert_calendar_entity(
        household_id: str,
        *,
        calendar_id: str | None = None,
        name: str,
        member_id: str | None = None,
        category: str = "general",
        source: str = "calendar_import",
    ) -> CalendarEntity:
        """Upsert a CalendarEntity by calendar_id (if set) or name."""
        now = datetime.now(timezone.utc)
        with users_session() as session:
            existing = None
            if calendar_id:
                existing = session.exec(
                    select(CalendarEntity)
                    .where(CalendarEntity.household_id == household_id,
                           CalendarEntity.calendar_id == calendar_id)
                ).first()
            if existing is None:
                existing = session.exec(
                    select(CalendarEntity)
                    .where(CalendarEntity.household_id == household_id,
                           CalendarEntity.name == name)
                ).first()

            if existing:
                existing.name = name
                existing.category = category
                if member_id is not None:
                    existing.member_id = member_id
                if calendar_id:
                    existing.calendar_id = calendar_id
                existing.updated_at = now
                session.add(existing)
                session.commit()
                session.refresh(existing)
                return existing

            cal = CalendarEntity(
                household_id=household_id,
                calendar_id=calendar_id,
                name=name,
                member_id=member_id,
                category=category,
                source=source,
            )
            session.add(cal)
            session.commit()
            session.refresh(cal)
            return cal

    @staticmethod
    def upsert_routine(
        household_id: str,
        *,
        name: str,
        description: str = "",
        kind: str = "",
        schedule_hint_json: str = "{}",
        source: str = "migration_seed",
    ) -> RoutineEntity:
        """Upsert a RoutineEntity by name."""
        now = datetime.now(timezone.utc)
        with users_session() as session:
            existing = session.exec(
                select(RoutineEntity)
                .where(RoutineEntity.household_id == household_id,
                       RoutineEntity.name == name)
            ).first()

            if existing:
                existing.description = description
                existing.kind = kind
                existing.schedule_hint_json = schedule_hint_json
                existing.updated_at = now
                session.add(existing)
                session.commit()
                session.refresh(existing)
                return existing

            routine = RoutineEntity(
                household_id=household_id,
                name=name,
                description=description,
                kind=kind,
                schedule_hint_json=schedule_hint_json,
                source=source,
            )
            session.add(routine)
            session.commit()
            session.refresh(routine)
            return routine

    @staticmethod
    def upsert_world_fact(
        household_id: str,
        *,
        scope: str,
        key: str,
        value: object,
        source: str = "migration_seed",
        overwrite: bool = False,
    ) -> WorldFact:
        """Upsert a WorldFact by (household_id, scope, key).

        If overwrite=False (default), existing facts are not touched.
        """
        now = datetime.now(timezone.utc)
        with users_session() as session:
            existing = session.exec(
                select(WorldFact)
                .where(WorldFact.household_id == household_id,
                       WorldFact.scope == scope,
                       WorldFact.key == key)
            ).first()

            if existing:
                if not overwrite:
                    return existing
                existing.value_json = json.dumps(value)
                existing.source = source
                existing.updated_at = now
                session.add(existing)
                session.commit()
                session.refresh(existing)
                return existing

            fact = WorldFact(
                household_id=household_id,
                scope=scope,
                key=key,
                value_json=json.dumps(value),
                source=source,
            )
            session.add(fact)
            session.commit()
            session.refresh(fact)
            return fact

    @staticmethod
    def upsert_interest(
        household_id: str,
        *,
        member_id: str,
        name: str,
        notes: str = "",
        source: str = "user_explicit",
    ) -> MemberInterest:
        """Upsert a MemberInterest by (member_id, name)."""
        now = datetime.now(timezone.utc)
        with users_session() as session:
            existing = session.exec(
                select(MemberInterest)
                .where(MemberInterest.member_id == member_id,
                       MemberInterest.name == name)
            ).first()

            if existing:
                existing.notes = notes
                existing.source = source
                existing.updated_at = now
                session.add(existing)
                session.commit()
                session.refresh(existing)
                return existing

            interest = MemberInterest(
                household_id=household_id,
                member_id=member_id,
                name=name,
                notes=notes,
                source=source,
            )
            session.add(interest)
            session.commit()
            session.refresh(interest)
            return interest

    @staticmethod
    def upsert_activity(
        household_id: str,
        *,
        member_id: str,
        name: str,
        schedule_hint: str = "",
        notes: str = "",
        source: str = "user_explicit",
    ) -> MemberActivity:
        """Upsert a MemberActivity by (member_id, name)."""
        now = datetime.now(timezone.utc)
        with users_session() as session:
            existing = session.exec(
                select(MemberActivity)
                .where(MemberActivity.member_id == member_id,
                       MemberActivity.name == name)
            ).first()

            if existing:
                existing.schedule_hint = schedule_hint or existing.schedule_hint
                existing.notes = notes or existing.notes
                existing.source = source
                existing.updated_at = now
                session.add(existing)
                session.commit()
                session.refresh(existing)
                return existing

            activity = MemberActivity(
                household_id=household_id,
                member_id=member_id,
                name=name,
                schedule_hint=schedule_hint,
                notes=notes,
                source=source,
            )
            session.add(activity)
            session.commit()
            session.refresh(activity)
            return activity

    @staticmethod
    def upsert_goal(
        household_id: str,
        *,
        member_id: str,
        name: str,
        notes: str = "",
        source: str = "user_explicit",
    ) -> MemberGoal:
        """Upsert a MemberGoal by (member_id, name)."""
        now = datetime.now(timezone.utc)
        with users_session() as session:
            existing = session.exec(
                select(MemberGoal)
                .where(MemberGoal.member_id == member_id,
                       MemberGoal.name == name)
            ).first()

            if existing:
                existing.notes = notes or existing.notes
                existing.source = source
                existing.updated_at = now
                session.add(existing)
                session.commit()
                session.refresh(existing)
                return existing

            goal = MemberGoal(
                household_id=household_id,
                member_id=member_id,
                name=name,
                notes=notes,
                source=source,
            )
            session.add(goal)
            session.commit()
            session.refresh(goal)
            return goal

    @staticmethod
    def upsert_relationship(
        household_id: str,
        *,
        subject_type: str,
        subject_id: str,
        predicate: str,
        object_type: str,
        object_id: str,
        metadata_json: str = "{}",
        confidence: float = 1.0,
        source: str = "user_explicit",
    ) -> Relationship:
        """Upsert a Relationship by full natural key."""
        now = datetime.now(timezone.utc)
        with users_session() as session:
            existing = session.exec(
                select(Relationship)
                .where(
                    Relationship.household_id == household_id,
                    Relationship.subject_type == subject_type,
                    Relationship.subject_id == subject_id,
                    Relationship.predicate == predicate,
                    Relationship.object_type == object_type,
                    Relationship.object_id == object_id,
                )
            ).first()

            if existing:
                existing.metadata_json = metadata_json
                existing.confidence = confidence
                existing.source = source
                existing.updated_at = now
                session.add(existing)
                session.commit()
                session.refresh(existing)
                return existing

            rel = Relationship(
                household_id=household_id,
                subject_type=subject_type,
                subject_id=subject_id,
                predicate=predicate,
                object_type=object_type,
                object_id=object_id,
                metadata_json=metadata_json,
                confidence=confidence,
                source=source,
            )
            session.add(rel)
            session.commit()
            session.refresh(rel)
            return rel

    # ------------------------------------------------------------------
    # Alias management
    # ------------------------------------------------------------------

    @staticmethod
    def add_alias(
        household_id: str, entity_type: str, entity_id: str, alias: str,
    ) -> bool:
        """Append an alias to an entity's aliases_json. Returns True if changed."""
        model_map: dict[str, type] = {
            "householdmember": HouseholdMember,
            "place": Place,
            "deviceentity": DeviceEntity,
        }
        model_cls = model_map.get(entity_type.lower())
        if model_cls is None:
            return False

        with users_session() as session:
            entity = session.get(model_cls, entity_id)
            if entity is None or getattr(entity, "household_id", None) != household_id:
                return False

            try:
                aliases = json.loads(entity.aliases_json) if entity.aliases_json else []
            except (json.JSONDecodeError, TypeError):
                aliases = []

            if alias.lower() in (a.lower() for a in aliases):
                return False

            aliases.append(alias)
            entity.aliases_json = json.dumps(aliases)
            entity.updated_at = datetime.now(timezone.utc)
            session.add(entity)
            session.commit()
        return True

    @staticmethod
    def remove_alias(
        household_id: str, entity_type: str, entity_id: str, alias: str,
    ) -> bool:
        """Remove an alias from an entity's aliases_json. Returns True if changed."""
        model_map: dict[str, type] = {
            "householdmember": HouseholdMember,
            "place": Place,
            "deviceentity": DeviceEntity,
        }
        model_cls = model_map.get(entity_type.lower())
        if model_cls is None:
            return False

        with users_session() as session:
            entity = session.get(model_cls, entity_id)
            if entity is None or getattr(entity, "household_id", None) != household_id:
                return False

            try:
                aliases = json.loads(entity.aliases_json) if entity.aliases_json else []
            except (json.JSONDecodeError, TypeError):
                aliases = []

            needle = alias.lower()
            new_aliases = [a for a in aliases if a.lower() != needle]
            if len(new_aliases) == len(aliases):
                return False

            entity.aliases_json = json.dumps(new_aliases)
            entity.updated_at = datetime.now(timezone.utc)
            session.add(entity)
            session.commit()
        return True

    # ------------------------------------------------------------------
    # Delete methods
    # ------------------------------------------------------------------

    _DELETABLE_MODELS: dict[str, type] = {
        "memberinterest": MemberInterest,
        "memberactivity": MemberActivity,
        "membergoal": MemberGoal,
        "routineentity": RoutineEntity,
        "relationship": Relationship,
        "worldfact": WorldFact,
    }

    @staticmethod
    def delete_entity(entity_type: str, entity_id: str) -> bool:
        """Delete a world-model entity by type and ID. Returns True if deleted."""
        model_cls = WorldModelRepository._DELETABLE_MODELS.get(entity_type.lower())
        if model_cls is None:
            return False

        with users_session() as session:
            entity = session.get(model_cls, entity_id)
            if entity is None:
                return False
            session.delete(entity)
            session.commit()
        return True

    @staticmethod
    def delete_interest(member_id: str, name: str) -> bool:
        with users_session() as session:
            item = session.exec(
                select(MemberInterest)
                .where(MemberInterest.member_id == member_id,
                       MemberInterest.name == name)
            ).first()
            if item is None:
                return False
            session.delete(item)
            session.commit()
        return True

    @staticmethod
    def delete_activity(member_id: str, name: str) -> bool:
        with users_session() as session:
            item = session.exec(
                select(MemberActivity)
                .where(MemberActivity.member_id == member_id,
                       MemberActivity.name == name)
            ).first()
            if item is None:
                return False
            session.delete(item)
            session.commit()
        return True

    @staticmethod
    def delete_goal(member_id: str, name: str) -> bool:
        with users_session() as session:
            item = session.exec(
                select(MemberGoal)
                .where(MemberGoal.member_id == member_id,
                       MemberGoal.name == name)
            ).first()
            if item is None:
                return False
            session.delete(item)
            session.commit()
        return True

    @staticmethod
    def delete_fact(household_id: str, scope: str, key: str) -> bool:
        with users_session() as session:
            item = session.exec(
                select(WorldFact)
                .where(WorldFact.household_id == household_id,
                       WorldFact.scope == scope,
                       WorldFact.key == key)
            ).first()
            if item is None:
                return False
            session.delete(item)
            session.commit()
        return True

    # ------------------------------------------------------------------
    # Proposals  (Phase 4)
    # ------------------------------------------------------------------

    @staticmethod
    def create_proposal(
        household_id: str,
        proposal_type: str,
        payload: dict,
        reason: str,
        confidence: float = 0.5,
        entity_type: str | None = None,
        entity_id: str | None = None,
        source_run_id: str | None = None,
        status: str = "pending",
    ) -> WorldModelProposal:
        p = WorldModelProposal(
            household_id=household_id,
            proposal_type=proposal_type,
            entity_type=entity_type,
            entity_id=entity_id,
            payload_json=json.dumps(payload),
            reason=reason,
            confidence=confidence,
            source_run_id=source_run_id,
            status=status,
            reviewed_at=datetime.now(timezone.utc) if status == "auto_applied" else None,
            reviewed_by="auto" if status == "auto_applied" else None,
        )
        with users_session() as session:
            session.add(p)
            session.commit()
            session.refresh(p)
        return p

    @staticmethod
    def get_pending_proposals(household_id: str) -> list[WorldModelProposal]:
        with users_session() as session:
            return list(
                session.exec(
                    select(WorldModelProposal)
                    .where(
                        WorldModelProposal.household_id == household_id,
                        WorldModelProposal.status == "pending",
                    )
                    .order_by(WorldModelProposal.created_at.desc())  # type: ignore[union-attr]
                ).all()
            )

    @staticmethod
    def get_recent_proposals(household_id: str, limit: int = 50) -> list[WorldModelProposal]:
        with users_session() as session:
            return list(
                session.exec(
                    select(WorldModelProposal)
                    .where(WorldModelProposal.household_id == household_id)
                    .order_by(WorldModelProposal.created_at.desc())  # type: ignore[union-attr]
                    .limit(limit)
                ).all()
            )

    @staticmethod
    def review_proposal(proposal_id: str, decision: str, reviewed_by: str = "admin") -> WorldModelProposal | None:
        """Accept or reject a proposal. If accepted, the caller applies the change."""
        with users_session() as session:
            p = session.get(WorldModelProposal, proposal_id)
            if p is None or p.status != "pending":
                return None
            p.status = decision  # "accepted" or "rejected"
            p.reviewed_at = datetime.now(timezone.utc)
            p.reviewed_by = reviewed_by
            session.add(p)
            session.commit()
            session.refresh(p)
            return p
