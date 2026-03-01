"""Initial schema for users.db

Revision ID: 0001_users
Revises:
Create Date: 2026-03-01

"""

from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op

revision: str = "0001_users"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = "users_db"
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    import os

    if os.environ.get("ALEMBIC_CURRENT_DB", "users") != "users":
        return

    op.create_table(
        "household",
        sa.Column("id", sqlmodel.AutoString(), nullable=False),
        sa.Column("name", sqlmodel.AutoString(), nullable=False),
        sa.Column("timezone", sqlmodel.AutoString(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "user",
        sa.Column("id", sqlmodel.AutoString(), nullable=False),
        sa.Column("household_id", sqlmodel.AutoString(), nullable=False),
        sa.Column("telegram_id", sa.Integer(), nullable=False),
        sa.Column("name", sqlmodel.AutoString(), nullable=False),
        sa.Column("is_admin", sa.Boolean(), nullable=False),
        sa.Column("preferred_channel", sqlmodel.AutoString(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["household_id"], ["household.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_user_household_id", "user", ["household_id"])
    op.create_index("ix_user_telegram_id", "user", ["telegram_id"], unique=True)

    op.create_table(
        "channelmapping",
        sa.Column("id", sqlmodel.AutoString(), nullable=False),
        sa.Column("user_id", sqlmodel.AutoString(), nullable=False),
        sa.Column("channel", sqlmodel.AutoString(), nullable=False),
        sa.Column("channel_user_id", sqlmodel.AutoString(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_channelmapping_user_id", "channelmapping", ["user_id"])

    op.create_table(
        "task",
        sa.Column("id", sqlmodel.AutoString(), nullable=False),
        sa.Column("household_id", sqlmodel.AutoString(), nullable=False),
        sa.Column("user_id", sqlmodel.AutoString(), nullable=False),
        sa.Column("title", sqlmodel.AutoString(), nullable=False),
        sa.Column("status", sqlmodel.AutoString(), nullable=False),
        sa.Column("steps", sqlmodel.AutoString(), nullable=False),
        sa.Column("current_step", sa.Integer(), nullable=False),
        sa.Column("context", sqlmodel.AutoString(), nullable=False),
        sa.Column("trigger_event_id", sqlmodel.AutoString(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_task_household_id", "task", ["household_id"])
    op.create_index("ix_task_user_id", "task", ["user_id"])


def downgrade() -> None:
    import os

    if os.environ.get("ALEMBIC_CURRENT_DB", "users") != "users":
        return

    op.drop_table("task")
    op.drop_table("channelmapping")
    op.drop_table("user")
    op.drop_table("household")
