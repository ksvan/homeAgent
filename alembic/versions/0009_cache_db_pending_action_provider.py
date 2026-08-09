"""Add provider to pendingaction table in cache.db

Revision ID: 0009_cache
Revises: 0008_cache
Create Date: 2026-08-09

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0009_cache"
down_revision: Union[str, None] = "0008_cache"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _is_cache(url: str) -> bool:
    return "cache" in url


def _col_exists(conn: sa.engine.Connection, table: str, col: str) -> bool:
    rows = conn.execute(sa.text(f"PRAGMA table_info({table})")).fetchall()
    return any(row[1] == col for row in rows)


def upgrade() -> None:
    conn = op.get_bind()
    url = conn.engine.url.render_as_string(hide_password=False)
    if not _is_cache(url):
        return
    if not _col_exists(conn, "pendingaction", "provider"):
        conn.execute(
            sa.text(
                "ALTER TABLE pendingaction ADD COLUMN provider VARCHAR NOT NULL DEFAULT 'homey'"
            )
        )


def downgrade() -> None:
    conn = op.get_bind()
    url = conn.engine.url.render_as_string(hide_password=False)
    if not _is_cache(url):
        return
    with op.batch_alter_table("pendingaction") as batch_op:
        batch_op.drop_column("provider")
