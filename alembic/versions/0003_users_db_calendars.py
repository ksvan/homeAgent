"""Add calendar table to users.db

Revision ID: 0003_users
Revises: 0002_users
Create Date: 2026-03-09

"""

import os
from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op

revision: str = "0003_users"
down_revision: Union[str, None] = "0002_users"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if os.environ.get("ALEMBIC_CURRENT_DB", "users") != "users":
        return

    op.create_table(
        "calendar",
        sa.Column("id", sqlmodel.AutoString(), nullable=False),
        sa.Column("household_id", sqlmodel.AutoString(), nullable=False),
        sa.Column("name", sqlmodel.AutoString(), nullable=False),
        sa.Column("url", sqlmodel.AutoString(), nullable=False),
        sa.Column("member_name", sqlmodel.AutoString(), nullable=True),
        sa.Column("category", sqlmodel.AutoString(), nullable=False, server_default="general"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_calendar_household_id", "calendar", ["household_id"])


def downgrade() -> None:
    if os.environ.get("ALEMBIC_CURRENT_DB", "users") != "users":
        return

    op.drop_index("ix_calendar_household_id", table_name="calendar")
    op.drop_table("calendar")
