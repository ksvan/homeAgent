"""Add ConversationTurn table for full tool-call history

Revision ID: 0003_memory
Revises: 0002_memory
Create Date: 2026-03-18

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003_memory"
down_revision: Union[str, None] = "0002_memory"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    import os

    if os.environ.get("ALEMBIC_CURRENT_DB", "memory") != "memory":
        return

    op.create_table(
        "conversationturn",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("messages_json", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_conversationturn_user_id", "conversationturn", ["user_id"])


def downgrade() -> None:
    import os

    if os.environ.get("ALEMBIC_CURRENT_DB", "memory") != "memory":
        return

    op.drop_index("ix_conversationturn_user_id", "conversationturn")
    op.drop_table("conversationturn")
