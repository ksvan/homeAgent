"""Add scheduledpromptlink table to users.db

Revision ID: 0011_users
Revises: 0010_users
Create Date: 2026-03-31

"""

import os
from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op

revision: str = "0011_users"
down_revision: Union[str, None] = "0010_users"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if os.environ.get("ALEMBIC_CURRENT_DB", "users") != "users":
        return

    op.create_table(
        "scheduledpromptlink",
        sa.Column("id", sqlmodel.AutoString(), nullable=False),
        sa.Column("prompt_id", sqlmodel.AutoString(), nullable=False),
        sa.Column("entity_type", sqlmodel.AutoString(), nullable=False),
        sa.Column("entity_id", sqlmodel.AutoString(), nullable=False),
        sa.Column("role", sqlmodel.AutoString(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["prompt_id"], ["scheduledprompt.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_scheduledpromptlink_prompt_id", "scheduledpromptlink", ["prompt_id"])


def downgrade() -> None:
    if os.environ.get("ALEMBIC_CURRENT_DB", "users") != "users":
        return

    op.drop_index("ix_scheduledpromptlink_prompt_id", "scheduledpromptlink")
    op.drop_table("scheduledpromptlink")
