"""Add world model proposals table to users.db

Revision ID: 0007_users
Revises: 0006_users
Create Date: 2026-03-29

"""

import os
from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op

revision: str = "0007_users"
down_revision: Union[str, None] = "0006_users"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if os.environ.get("ALEMBIC_CURRENT_DB", "users") != "users":
        return

    op.create_table(
        "worldmodelproposal",
        sa.Column("id", sqlmodel.AutoString(), nullable=False),
        sa.Column("household_id", sqlmodel.AutoString(), nullable=False),
        sa.Column("proposal_type", sqlmodel.AutoString(), nullable=False),
        sa.Column("entity_type", sqlmodel.AutoString(), nullable=True),
        sa.Column("entity_id", sqlmodel.AutoString(), nullable=True),
        sa.Column("payload_json", sqlmodel.AutoString(), nullable=False),
        sa.Column("reason", sqlmodel.AutoString(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("source_run_id", sqlmodel.AutoString(), nullable=True),
        sa.Column("status", sqlmodel.AutoString(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(), nullable=True),
        sa.Column("reviewed_by", sqlmodel.AutoString(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_worldmodelproposal_household_id", "worldmodelproposal", ["household_id"])
    op.create_index("ix_worldmodelproposal_status", "worldmodelproposal", ["status"])


def downgrade() -> None:
    if os.environ.get("ALEMBIC_CURRENT_DB", "users") != "users":
        return

    op.drop_index("ix_worldmodelproposal_status", "worldmodelproposal")
    op.drop_index("ix_worldmodelproposal_household_id", "worldmodelproposal")
    op.drop_table("worldmodelproposal")
