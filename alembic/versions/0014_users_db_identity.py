"""Add onboarding_complete, name_user_asserted, ChannelMapping unique constraint

Revision ID: 0014_users
Revises: 0013_users
Create Date: 2026-05-07

"""

import uuid
from datetime import datetime, timezone
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0014_users"
down_revision: Union[str, None] = "0013_users"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(conn: sa.engine.Connection, name: str) -> bool:
    return bool(conn.execute(
        sa.text("SELECT name FROM sqlite_master WHERE type='table' AND name=:n"),
        {"n": name},
    ).fetchone())


def _col_exists(conn: sa.engine.Connection, table: str, col: str) -> bool:
    rows = conn.execute(sa.text(f"PRAGMA table_info({table})")).fetchall()
    return any(row[1] == col for row in rows)


def upgrade() -> None:
    conn = op.get_bind()

    if _table_exists(conn, "user"):
        if not _col_exists(conn, "user", "onboarding_complete"):
            conn.execute(sa.text(
                "ALTER TABLE user ADD COLUMN onboarding_complete BOOLEAN NOT NULL DEFAULT 0"
            ))
    # else: table will be created fresh by SQLModel with the column already in the model.

    if _table_exists(conn, "householdmember"):
        if not _col_exists(conn, "householdmember", "name_user_asserted"):
            conn.execute(sa.text(
                "ALTER TABLE householdmember "
                "ADD COLUMN name_user_asserted BOOLEAN NOT NULL DEFAULT 0"
            ))

    if _table_exists(conn, "channelmapping"):
        # Backfill telegram ChannelMapping rows for existing users.
        if _table_exists(conn, "user"):
            users = conn.execute(sa.text("SELECT id, telegram_id FROM user")).fetchall()
            for user_id, telegram_id in users:
                existing = conn.execute(
                    sa.text(
                        "SELECT id FROM channelmapping "
                        "WHERE channel = 'telegram' AND channel_user_id = :cid"
                    ),
                    {"cid": str(telegram_id)},
                ).fetchone()
                if not existing:
                    conn.execute(
                        sa.text(
                            "INSERT INTO channelmapping "
                            "(id, user_id, channel, channel_user_id, created_at) "
                            "VALUES (:id, :user_id, 'telegram', :cid, :now)"
                        ),
                        {
                            "id": str(uuid.uuid4()),
                            "user_id": user_id,
                            "cid": str(telegram_id),
                            "now": datetime.now(timezone.utc).isoformat(),
                        },
                    )

        # Deduplicate before adding constraint.
        conn.execute(sa.text(
            "DELETE FROM channelmapping WHERE id NOT IN ("
            "  SELECT id FROM channelmapping GROUP BY channel, channel_user_id"
            "  HAVING id = MIN(id)"
            ")"
        ))

        # Add unique constraint only if not already present.
        indexes = conn.execute(
            sa.text("PRAGMA index_list(channelmapping)")
        ).fetchall()
        index_names = {row[1] for row in indexes}
        if "uq_channelmapping_channel_user" not in index_names:
            with op.batch_alter_table("channelmapping") as batch_op:
                batch_op.create_unique_constraint(
                    "uq_channelmapping_channel_user",
                    ["channel", "channel_user_id"],
                )


def downgrade() -> None:
    conn = op.get_bind()
    if _table_exists(conn, "channelmapping"):
        with op.batch_alter_table("channelmapping") as batch_op:
            batch_op.drop_constraint("uq_channelmapping_channel_user", type_="unique")
    if _table_exists(conn, "householdmember") and _col_exists(
        conn, "householdmember", "name_user_asserted"
    ):
        conn.execute(sa.text("ALTER TABLE householdmember DROP COLUMN name_user_asserted"))
    if _table_exists(conn, "user") and _col_exists(conn, "user", "onboarding_complete"):
        conn.execute(sa.text("ALTER TABLE user DROP COLUMN onboarding_complete"))
