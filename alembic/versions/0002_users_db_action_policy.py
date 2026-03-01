"""Add action_policy table to users.db

Revision ID: 0002_users
Revises: 0001_users
Create Date: 2026-03-01

"""

import os
from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op

revision: str = "0002_users"
down_revision: Union[str, None] = "0001_users"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if os.environ.get("ALEMBIC_CURRENT_DB", "users") != "users":
        return

    op.create_table(
        "actionpolicy",
        sa.Column("id", sqlmodel.AutoString(), nullable=False),
        sa.Column("name", sqlmodel.AutoString(), nullable=False),
        sa.Column("tool_pattern", sqlmodel.AutoString(), nullable=False),
        sa.Column("arg_conditions", sqlmodel.AutoString(), nullable=False, server_default="{}"),
        sa.Column("impact_level", sqlmodel.AutoString(), nullable=False, server_default="medium"),
        sa.Column("requires_confirm", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("confirm_message", sqlmodel.AutoString(), nullable=False, server_default=""),
        sa.Column("cooldown_seconds", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.create_index("ix_actionpolicy_name", "actionpolicy", ["name"], unique=True)
    op.create_index("ix_actionpolicy_tool_pattern", "actionpolicy", ["tool_pattern"])


def downgrade() -> None:
    if os.environ.get("ALEMBIC_CURRENT_DB", "users") != "users":
        return

    op.drop_index("ix_actionpolicy_tool_pattern", table_name="actionpolicy")
    op.drop_index("ix_actionpolicy_name", table_name="actionpolicy")
    op.drop_table("actionpolicy")
