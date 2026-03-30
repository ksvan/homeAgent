"""Extend task table + add taskstep table to users.db

Revision ID: 0008_users
Revises: 0007_users
Create Date: 2026-03-28

"""

import os
from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op

revision: str = "0008_users"
down_revision: Union[str, None] = "0007_users"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if os.environ.get("ALEMBIC_CURRENT_DB", "users") != "users":
        return

    # -- Extend task table with new columns --
    op.add_column("task", sa.Column("task_kind", sqlmodel.AutoString(), nullable=True))
    op.add_column("task", sa.Column("summary", sqlmodel.AutoString(), nullable=True))
    op.add_column("task", sa.Column("awaiting_input_hint", sqlmodel.AutoString(), nullable=True))
    op.add_column("task", sa.Column("resume_after", sa.DateTime(), nullable=True))
    op.add_column("task", sa.Column("last_agent_run_id", sqlmodel.AutoString(), nullable=True))

    # -- TaskStep table --
    op.create_table(
        "taskstep",
        sa.Column("id", sqlmodel.AutoString(), nullable=False),
        sa.Column("task_id", sqlmodel.AutoString(), nullable=False),
        sa.Column("step_index", sa.Integer(), nullable=False),
        sa.Column("title", sqlmodel.AutoString(), nullable=False),
        sa.Column("status", sqlmodel.AutoString(), nullable=False),
        sa.Column("step_type", sqlmodel.AutoString(), nullable=False),
        sa.Column("details_json", sqlmodel.AutoString(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["task_id"], ["task.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_taskstep_task_id", "taskstep", ["task_id"])


def downgrade() -> None:
    if os.environ.get("ALEMBIC_CURRENT_DB", "users") != "users":
        return

    op.drop_index("ix_taskstep_task_id", "taskstep")
    op.drop_table("taskstep")

    op.drop_column("task", "last_agent_run_id")
    op.drop_column("task", "resume_after")
    op.drop_column("task", "awaiting_input_hint")
    op.drop_column("task", "summary")
    op.drop_column("task", "task_kind")
