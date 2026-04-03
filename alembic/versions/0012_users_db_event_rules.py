"""Add eventrule table to users.db

Revision ID: 0012_users
Revises: 0011_users
Create Date: 2026-04-03

"""

import os
from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op

revision: str = "0012_users"
down_revision: Union[str, None] = "0011_users"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if os.environ.get("ALEMBIC_CURRENT_DB", "users") != "users":
        return

    op.create_table(
        "eventrule",
        sa.Column("id", sqlmodel.AutoString(), nullable=False),
        sa.Column("household_id", sqlmodel.AutoString(), nullable=False),
        sa.Column("user_id", sqlmodel.AutoString(), nullable=False),
        sa.Column("channel_user_id", sqlmodel.AutoString(), nullable=False),
        sa.Column("name", sqlmodel.AutoString(), nullable=False),
        sa.Column("source", sqlmodel.AutoString(), nullable=False),
        sa.Column("event_type", sqlmodel.AutoString(), nullable=False),
        sa.Column("entity_id", sqlmodel.AutoString(), nullable=False),
        sa.Column("capability", sqlmodel.AutoString(), nullable=True),
        sa.Column("value_filter_json", sqlmodel.AutoString(), nullable=True),
        sa.Column("condition_json", sqlmodel.AutoString(), nullable=True),
        sa.Column("cooldown_minutes", sa.Integer(), nullable=False),
        sa.Column("prompt_template", sqlmodel.AutoString(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_eventrule_household_id", "eventrule", ["household_id"])
    op.create_index("ix_eventrule_user_id", "eventrule", ["user_id"])


def downgrade() -> None:
    if os.environ.get("ALEMBIC_CURRENT_DB", "users") != "users":
        return

    op.drop_index("ix_eventrule_user_id", "eventrule")
    op.drop_index("ix_eventrule_household_id", "eventrule")
    op.drop_table("eventrule")
