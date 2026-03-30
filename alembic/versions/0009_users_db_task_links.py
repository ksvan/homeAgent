"""Add tasklink table to users.db

Revision ID: 0009_users
Revises: 0008_users
Create Date: 2026-03-28

"""

import os
from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op

revision: str = "0009_users"
down_revision: Union[str, None] = "0008_users"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if os.environ.get("ALEMBIC_CURRENT_DB", "users") != "users":
        return

    op.create_table(
        "tasklink",
        sa.Column("id", sqlmodel.AutoString(), nullable=False),
        sa.Column("task_id", sqlmodel.AutoString(), nullable=False),
        sa.Column("entity_type", sqlmodel.AutoString(), nullable=False),
        sa.Column("entity_id", sqlmodel.AutoString(), nullable=False),
        sa.Column("role", sqlmodel.AutoString(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["task_id"], ["task.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_tasklink_task_id", "tasklink", ["task_id"])


def downgrade() -> None:
    if os.environ.get("ALEMBIC_CURRENT_DB", "users") != "users":
        return

    op.drop_index("ix_tasklink_task_id", "tasklink")
    op.drop_table("tasklink")
