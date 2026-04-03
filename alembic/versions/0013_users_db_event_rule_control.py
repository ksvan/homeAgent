"""Add control-loop fields to eventrule

Revision ID: 0013_users
Revises: 0012_users
Create Date: 2026-04-03

"""

import os
from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op

revision: str = "0013_users"
down_revision: Union[str, None] = "0012_users"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if os.environ.get("ALEMBIC_CURRENT_DB", "users") != "users":
        return

    op.add_column(
        "eventrule",
        sa.Column(
            "run_mode",
            sqlmodel.AutoString(),
            nullable=False,
            server_default="notify_only",
        ),
    )
    op.add_column(
        "eventrule",
        sa.Column("task_kind_default", sqlmodel.AutoString(), nullable=True),
    )
    op.add_column(
        "eventrule",
        sa.Column("correlation_key_tpl", sqlmodel.AutoString(), nullable=True),
    )
    op.add_column(
        "eventrule",
        sa.Column("last_triggered_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    if os.environ.get("ALEMBIC_CURRENT_DB", "users") != "users":
        return

    op.drop_column("eventrule", "last_triggered_at")
    op.drop_column("eventrule", "correlation_key_tpl")
    op.drop_column("eventrule", "task_kind_default")
    op.drop_column("eventrule", "run_mode")
