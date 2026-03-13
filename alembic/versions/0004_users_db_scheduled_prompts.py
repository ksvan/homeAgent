"""Add scheduled_prompt table to users.db

Revision ID: 0004_users
Revises: 0003_users
Create Date: 2026-03-10

"""

import os
from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op

revision: str = "0004_users"
down_revision: Union[str, None] = "0003_users"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if os.environ.get("ALEMBIC_CURRENT_DB", "users") != "users":
        return

    op.create_table(
        "scheduledprompt",
        sa.Column("id", sqlmodel.AutoString(), nullable=False),
        sa.Column("household_id", sqlmodel.AutoString(), nullable=False),
        sa.Column("user_id", sqlmodel.AutoString(), nullable=False),
        sa.Column("channel_user_id", sqlmodel.AutoString(), nullable=False),
        sa.Column("name", sqlmodel.AutoString(), nullable=False),
        sa.Column("prompt", sqlmodel.AutoString(), nullable=False),
        sa.Column("recurrence", sqlmodel.AutoString(), nullable=False),
        sa.Column("time_of_day", sqlmodel.AutoString(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_scheduledprompt_household_id", "scheduledprompt", ["household_id"])
    op.create_index("ix_scheduledprompt_user_id", "scheduledprompt", ["user_id"])


def downgrade() -> None:
    if os.environ.get("ALEMBIC_CURRENT_DB", "users") != "users":
        return

    op.drop_index("ix_scheduledprompt_user_id", table_name="scheduledprompt")
    op.drop_index("ix_scheduledprompt_household_id", table_name="scheduledprompt")
    op.drop_table("scheduledprompt")
