from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Field, SQLModel, UniqueConstraint


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _uuid() -> str:
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# Household World Model — Phase 1 (read-only bootstrap)
# All tables live in users.db.
# ---------------------------------------------------------------------------


class HouseholdMember(SQLModel, table=True):
    id: str = Field(default_factory=_uuid, primary_key=True)
    household_id: str = Field(index=True)
    user_id: Optional[str] = Field(default=None, index=True)  # nullable — children/guests
    name: str
    aliases_json: str = "[]"  # JSON list of alternative names
    role: str = "member"  # "admin" | "member" | "child" | "guest"
    is_active: bool = True
    source: str = "migration_seed"
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class MemberInterest(SQLModel, table=True):
    id: str = Field(default_factory=_uuid, primary_key=True)
    member_id: str = Field(index=True)
    household_id: str = Field(index=True)
    name: str  # e.g. "football", "gaming", "piano"
    notes: str = ""
    source: str = "migration_seed"
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class MemberGoal(SQLModel, table=True):
    id: str = Field(default_factory=_uuid, primary_key=True)
    member_id: str = Field(index=True)
    household_id: str = Field(index=True)
    name: str  # e.g. "practice piano daily"
    status: str = "active"  # "active" | "completed" | "paused"
    notes: str = ""
    source: str = "migration_seed"
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class MemberActivity(SQLModel, table=True):
    id: str = Field(default_factory=_uuid, primary_key=True)
    member_id: str = Field(index=True)
    household_id: str = Field(index=True)
    name: str  # e.g. "football practice", "school"
    schedule_hint: str = ""  # freeform, e.g. "Tue/Thu 17:00-18:30"
    notes: str = ""
    source: str = "migration_seed"
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class Place(SQLModel, table=True):
    id: str = Field(default_factory=_uuid, primary_key=True)
    household_id: str = Field(index=True)
    name: str
    aliases_json: str = "[]"
    kind: str = "room"  # "room" | "floor" | "zone" | "outdoor"
    parent_place_id: Optional[str] = None  # self-referential hierarchy
    external_zone_id: Optional[str] = Field(default=None, index=True)  # Homey zone ID
    source: str = "migration_seed"
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class DeviceEntity(SQLModel, table=True):
    id: str = Field(default_factory=_uuid, primary_key=True)
    household_id: str = Field(index=True)
    external_device_id: Optional[str] = Field(default=None, index=True)  # Homey device ID
    name: str
    aliases_json: str = "[]"
    device_type: str = ""  # "light", "thermostat", "sensor", "plug", etc.
    place_id: Optional[str] = None  # FK place.id
    capabilities_json: str = "[]"
    is_controllable: bool = True
    source: str = "migration_seed"
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class CalendarEntity(SQLModel, table=True):
    id: str = Field(default_factory=_uuid, primary_key=True)
    household_id: str = Field(index=True)
    calendar_id: Optional[str] = Field(default=None, index=True)  # FK calendar.id
    name: str
    member_id: Optional[str] = None  # FK householdmember.id
    category: str = "general"
    is_active: bool = True
    source: str = "migration_seed"
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class RoutineEntity(SQLModel, table=True):
    id: str = Field(default_factory=_uuid, primary_key=True)
    household_id: str = Field(index=True)
    name: str  # "night mode", "school pickup"
    description: str = ""
    kind: str = ""  # "mode" | "schedule" | "procedure"
    schedule_hint_json: str = "{}"
    source: str = "migration_seed"
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class Relationship(SQLModel, table=True):
    id: str = Field(default_factory=_uuid, primary_key=True)
    household_id: str = Field(index=True)
    subject_type: str  # table name: "householdmember", "deviceentity", etc.
    subject_id: str
    predicate: str  # "member_has_calendar", "device_in_place", etc.
    object_type: str
    object_id: str
    metadata_json: str = "{}"
    confidence: float = 1.0
    source: str = "migration_seed"
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class WorldFact(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("household_id", "scope", "key"),)

    id: str = Field(default_factory=_uuid, primary_key=True)
    household_id: str = Field(index=True)
    scope: str  # "household" | "device" | "routine" | "member"
    key: str  # "night_mode.lights", "default_language"
    value_json: str  # JSON-encoded value
    confidence: float = 1.0
    source: str = "migration_seed"
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


# ---------------------------------------------------------------------------
# Phase 4 — World Model Proposals
# ---------------------------------------------------------------------------


class WorldModelProposal(SQLModel, table=True):
    id: str = Field(default_factory=_uuid, primary_key=True)
    household_id: str = Field(index=True)
    proposal_type: str  # "fact" | "alias" | "interest" | "activity" | "goal" | "routine"
    entity_type: Optional[str] = None  # target entity type (e.g. "device", "member")
    entity_id: Optional[str] = None  # target entity id
    payload_json: str  # JSON with proposed change details
    reason: str  # one-sentence explanation from the extractor
    confidence: float = 0.5
    source_run_id: Optional[str] = None  # agent run that spawned this
    status: str = Field(default="pending", index=True)  # pending | accepted | rejected | auto_applied
    created_at: datetime = Field(default_factory=_now)
    reviewed_at: Optional[datetime] = None
    reviewed_by: Optional[str] = None  # "admin" | "auto"
