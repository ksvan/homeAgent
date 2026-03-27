"""Add household world model tables to users.db

Revision ID: 0006_users
Revises: 0005_users
Create Date: 2026-03-27

"""

import os
from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op

revision: str = "0006_users"
down_revision: Union[str, None] = "0005_users"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if os.environ.get("ALEMBIC_CURRENT_DB", "users") != "users":
        return

    # -- HouseholdMember --
    op.create_table(
        "householdmember",
        sa.Column("id", sqlmodel.AutoString(), nullable=False),
        sa.Column("household_id", sqlmodel.AutoString(), nullable=False),
        sa.Column("user_id", sqlmodel.AutoString(), nullable=True),
        sa.Column("name", sqlmodel.AutoString(), nullable=False),
        sa.Column("aliases_json", sqlmodel.AutoString(), nullable=False, server_default="[]"),
        sa.Column("role", sqlmodel.AutoString(), nullable=False, server_default="member"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("source", sqlmodel.AutoString(), nullable=False, server_default="migration_seed"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_householdmember_household_id", "householdmember", ["household_id"])
    op.create_index("ix_householdmember_user_id", "householdmember", ["user_id"])

    # -- MemberInterest --
    op.create_table(
        "memberinterest",
        sa.Column("id", sqlmodel.AutoString(), nullable=False),
        sa.Column("member_id", sqlmodel.AutoString(), nullable=False),
        sa.Column("household_id", sqlmodel.AutoString(), nullable=False),
        sa.Column("name", sqlmodel.AutoString(), nullable=False),
        sa.Column("notes", sqlmodel.AutoString(), nullable=False, server_default=""),
        sa.Column("source", sqlmodel.AutoString(), nullable=False, server_default="migration_seed"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_memberinterest_member_id", "memberinterest", ["member_id"])
    op.create_index("ix_memberinterest_household_id", "memberinterest", ["household_id"])

    # -- MemberGoal --
    op.create_table(
        "membergoal",
        sa.Column("id", sqlmodel.AutoString(), nullable=False),
        sa.Column("member_id", sqlmodel.AutoString(), nullable=False),
        sa.Column("household_id", sqlmodel.AutoString(), nullable=False),
        sa.Column("name", sqlmodel.AutoString(), nullable=False),
        sa.Column("status", sqlmodel.AutoString(), nullable=False, server_default="active"),
        sa.Column("notes", sqlmodel.AutoString(), nullable=False, server_default=""),
        sa.Column("source", sqlmodel.AutoString(), nullable=False, server_default="migration_seed"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_membergoal_member_id", "membergoal", ["member_id"])
    op.create_index("ix_membergoal_household_id", "membergoal", ["household_id"])

    # -- MemberActivity --
    op.create_table(
        "memberactivity",
        sa.Column("id", sqlmodel.AutoString(), nullable=False),
        sa.Column("member_id", sqlmodel.AutoString(), nullable=False),
        sa.Column("household_id", sqlmodel.AutoString(), nullable=False),
        sa.Column("name", sqlmodel.AutoString(), nullable=False),
        sa.Column("schedule_hint", sqlmodel.AutoString(), nullable=False, server_default=""),
        sa.Column("notes", sqlmodel.AutoString(), nullable=False, server_default=""),
        sa.Column("source", sqlmodel.AutoString(), nullable=False, server_default="migration_seed"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_memberactivity_member_id", "memberactivity", ["member_id"])
    op.create_index("ix_memberactivity_household_id", "memberactivity", ["household_id"])

    # -- Place --
    op.create_table(
        "place",
        sa.Column("id", sqlmodel.AutoString(), nullable=False),
        sa.Column("household_id", sqlmodel.AutoString(), nullable=False),
        sa.Column("name", sqlmodel.AutoString(), nullable=False),
        sa.Column("aliases_json", sqlmodel.AutoString(), nullable=False, server_default="[]"),
        sa.Column("kind", sqlmodel.AutoString(), nullable=False, server_default="room"),
        sa.Column("parent_place_id", sqlmodel.AutoString(), nullable=True),
        sa.Column("external_zone_id", sqlmodel.AutoString(), nullable=True),
        sa.Column("source", sqlmodel.AutoString(), nullable=False, server_default="migration_seed"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_place_household_id", "place", ["household_id"])
    op.create_index("ix_place_external_zone_id", "place", ["external_zone_id"])

    # -- DeviceEntity --
    op.create_table(
        "deviceentity",
        sa.Column("id", sqlmodel.AutoString(), nullable=False),
        sa.Column("household_id", sqlmodel.AutoString(), nullable=False),
        sa.Column("external_device_id", sqlmodel.AutoString(), nullable=True),
        sa.Column("name", sqlmodel.AutoString(), nullable=False),
        sa.Column("aliases_json", sqlmodel.AutoString(), nullable=False, server_default="[]"),
        sa.Column("device_type", sqlmodel.AutoString(), nullable=False, server_default=""),
        sa.Column("place_id", sqlmodel.AutoString(), nullable=True),
        sa.Column("capabilities_json", sqlmodel.AutoString(), nullable=False, server_default="[]"),
        sa.Column("is_controllable", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("source", sqlmodel.AutoString(), nullable=False, server_default="migration_seed"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_deviceentity_household_id", "deviceentity", ["household_id"])
    op.create_index("ix_deviceentity_external_device_id", "deviceentity", ["external_device_id"])

    # -- CalendarEntity --
    op.create_table(
        "calendarentity",
        sa.Column("id", sqlmodel.AutoString(), nullable=False),
        sa.Column("household_id", sqlmodel.AutoString(), nullable=False),
        sa.Column("calendar_id", sqlmodel.AutoString(), nullable=True),
        sa.Column("name", sqlmodel.AutoString(), nullable=False),
        sa.Column("member_id", sqlmodel.AutoString(), nullable=True),
        sa.Column("category", sqlmodel.AutoString(), nullable=False, server_default="general"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("source", sqlmodel.AutoString(), nullable=False, server_default="migration_seed"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_calendarentity_household_id", "calendarentity", ["household_id"])
    op.create_index("ix_calendarentity_calendar_id", "calendarentity", ["calendar_id"])

    # -- RoutineEntity --
    op.create_table(
        "routineentity",
        sa.Column("id", sqlmodel.AutoString(), nullable=False),
        sa.Column("household_id", sqlmodel.AutoString(), nullable=False),
        sa.Column("name", sqlmodel.AutoString(), nullable=False),
        sa.Column("description", sqlmodel.AutoString(), nullable=False, server_default=""),
        sa.Column("kind", sqlmodel.AutoString(), nullable=False, server_default=""),
        sa.Column("schedule_hint_json", sqlmodel.AutoString(), nullable=False, server_default="{}"),
        sa.Column("source", sqlmodel.AutoString(), nullable=False, server_default="migration_seed"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_routineentity_household_id", "routineentity", ["household_id"])

    # -- Relationship --
    op.create_table(
        "relationship",
        sa.Column("id", sqlmodel.AutoString(), nullable=False),
        sa.Column("household_id", sqlmodel.AutoString(), nullable=False),
        sa.Column("subject_type", sqlmodel.AutoString(), nullable=False),
        sa.Column("subject_id", sqlmodel.AutoString(), nullable=False),
        sa.Column("predicate", sqlmodel.AutoString(), nullable=False),
        sa.Column("object_type", sqlmodel.AutoString(), nullable=False),
        sa.Column("object_id", sqlmodel.AutoString(), nullable=False),
        sa.Column("metadata_json", sqlmodel.AutoString(), nullable=False, server_default="{}"),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="1.0"),
        sa.Column("source", sqlmodel.AutoString(), nullable=False, server_default="migration_seed"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_relationship_household_id", "relationship", ["household_id"])
    op.create_index(
        "ix_relationship_subject",
        "relationship",
        ["household_id", "subject_type", "subject_id"],
    )
    op.create_index(
        "ix_relationship_object",
        "relationship",
        ["household_id", "object_type", "object_id"],
    )

    # -- WorldFact --
    op.create_table(
        "worldfact",
        sa.Column("id", sqlmodel.AutoString(), nullable=False),
        sa.Column("household_id", sqlmodel.AutoString(), nullable=False),
        sa.Column("scope", sqlmodel.AutoString(), nullable=False),
        sa.Column("key", sqlmodel.AutoString(), nullable=False),
        sa.Column("value_json", sqlmodel.AutoString(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="1.0"),
        sa.Column("source", sqlmodel.AutoString(), nullable=False, server_default="migration_seed"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("household_id", "scope", "key"),
    )
    op.create_index("ix_worldfact_household_id", "worldfact", ["household_id"])


def downgrade() -> None:
    if os.environ.get("ALEMBIC_CURRENT_DB", "users") != "users":
        return

    op.drop_table("worldfact")
    op.drop_table("relationship")
    op.drop_table("routineentity")
    op.drop_table("calendarentity")
    op.drop_table("deviceentity")
    op.drop_table("place")
    op.drop_table("memberactivity")
    op.drop_table("membergoal")
    op.drop_table("memberinterest")
    op.drop_table("householdmember")
