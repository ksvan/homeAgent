"""Extend scheduledprompt with behaviour metadata and create scheduledpromptrun table

Revision ID: 0010_users
Revises: 0009_users
Create Date: 2026-03-31

"""

import os
from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op

revision: str = "0010_users"
down_revision: Union[str, None] = "0009_users"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if os.environ.get("ALEMBIC_CURRENT_DB", "users") != "users":
        return

    # --- Extend scheduledprompt with behaviour metadata + last-run state ---
    for col_name, col_type in [
        ("behavior_kind", sqlmodel.AutoString()),
        ("goal", sqlmodel.AutoString()),
        ("config_json", sqlmodel.AutoString()),
        ("delivery_policy_json", sqlmodel.AutoString()),
        ("last_status", sqlmodel.AutoString()),
        ("last_result_hash", sqlmodel.AutoString()),
        ("last_result_preview", sqlmodel.AutoString()),
    ]:
        op.add_column("scheduledprompt", sa.Column(col_name, col_type, nullable=True))

    for col_name in ("last_fired_at", "last_delivered_at"):
        op.add_column("scheduledprompt", sa.Column(col_name, sa.DateTime(), nullable=True))

    # --- Create scheduledpromptrun audit table ---
    op.create_table(
        "scheduledpromptrun",
        sa.Column("id", sqlmodel.AutoString(), nullable=False),
        sa.Column("prompt_id", sqlmodel.AutoString(), nullable=False),
        sa.Column("fired_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("status", sqlmodel.AutoString(), nullable=False),
        sa.Column("skip_reason", sqlmodel.AutoString(), nullable=True),
        sa.Column("run_id", sqlmodel.AutoString(), nullable=True),
        sa.Column("output_hash", sqlmodel.AutoString(), nullable=True),
        sa.Column("output_preview", sqlmodel.AutoString(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["prompt_id"], ["scheduledprompt.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_scheduledpromptrun_prompt_id", "scheduledpromptrun", ["prompt_id"])


def downgrade() -> None:
    if os.environ.get("ALEMBIC_CURRENT_DB", "users") != "users":
        return

    op.drop_index("ix_scheduledpromptrun_prompt_id", "scheduledpromptrun")
    op.drop_table("scheduledpromptrun")

    for col_name in (
        "behavior_kind", "goal", "config_json", "delivery_policy_json",
        "last_fired_at", "last_delivered_at", "last_status",
        "last_result_hash", "last_result_preview",
    ):
        op.drop_column("scheduledprompt", col_name)
