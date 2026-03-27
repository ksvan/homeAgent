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
