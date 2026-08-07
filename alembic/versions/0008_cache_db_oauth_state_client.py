"""Add client_id/client_secret to oauthstate table in cache.db

Revision ID: 0008_cache
Revises: 0007_cache
Create Date: 2026-08-07

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0008_cache"
down_revision: Union[str, None] = "0007_cache"
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
    if not _col_exists(conn, "oauthstate", "client_id"):
        conn.execute(
            sa.text("ALTER TABLE oauthstate ADD COLUMN client_id VARCHAR NOT NULL DEFAULT ''")
        )
    if not _col_exists(conn, "oauthstate", "client_secret"):
        conn.execute(
            sa.text("ALTER TABLE oauthstate ADD COLUMN client_secret VARCHAR NOT NULL DEFAULT ''")
        )


def downgrade() -> None:
    conn = op.get_bind()
    url = conn.engine.url.render_as_string(hide_password=False)
    if not _is_cache(url):
        return
    with op.batch_alter_table("oauthstate") as batch_op:
        batch_op.drop_column("client_secret")
        batch_op.drop_column("client_id")
