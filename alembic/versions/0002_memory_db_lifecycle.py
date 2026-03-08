"""Add importance tier and last_used_at to episodicmemory

Revision ID: 0002_memory
Revises: 0001_memory
Create Date: 2026-03-07

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002_memory"
down_revision: Union[str, None] = "0001_memory"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    import os

    if os.environ.get("ALEMBIC_CURRENT_DB", "memory") != "memory":
        return

    op.add_column(
        "episodicmemory",
        sa.Column("importance", sa.String(), nullable=False, server_default="normal"),
    )
    op.add_column(
        "episodicmemory",
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    import os

    if os.environ.get("ALEMBIC_CURRENT_DB", "memory") != "memory":
        return

    op.drop_column("episodicmemory", "last_used_at")
    op.drop_column("episodicmemory", "importance")
