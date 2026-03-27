"""Add run_at column to scheduledprompt table in users.db

Revision ID: 0005_users
Revises: 0004_users
Create Date: 2026-03-27

"""

import os
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005_users"
down_revision: Union[str, None] = "0004_users"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if os.environ.get("ALEMBIC_CURRENT_DB", "users") != "users":
        return

    op.add_column(
        "scheduledprompt",
        sa.Column("run_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    if os.environ.get("ALEMBIC_CURRENT_DB", "users") != "users":
        return

    op.drop_column("scheduledprompt", "run_at")
