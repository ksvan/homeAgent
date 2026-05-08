"""Add member_id to EpisodicMemory

Revision ID: 0004_memory
Revises: 0003_memory
Create Date: 2026-05-07

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004_memory"
down_revision: Union[str, None] = "0003_memory"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()

    table_exists = conn.execute(
        sa.text(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name='episodicmemory'"
        )
    ).fetchone()

    if not table_exists:
        # Table will be created fresh by SQLModel with member_id already in schema.
        return

    cols = conn.execute(sa.text("PRAGMA table_info(episodicmemory)")).fetchall()
    existing = {row[1] for row in cols}
    if "member_id" not in existing:
        conn.execute(
            sa.text("ALTER TABLE episodicmemory ADD COLUMN member_id VARCHAR")
        )
        op.create_index("ix_episodicmemory_member_id", "episodicmemory", ["member_id"])


def downgrade() -> None:
    op.drop_index("ix_episodicmemory_member_id", "episodicmemory")
