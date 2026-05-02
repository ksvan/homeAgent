"""Add wine cellar tables to cache.db

Revision ID: 0002_cache
Revises: 0001_cache
Create Date: 2026-04-28

"""

import os
from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op

revision: str = "0002_cache"
down_revision: Union[str, None] = "0001_cache"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if os.environ.get("ALEMBIC_CURRENT_DB", "cache") != "cache":
        return

    op.create_table(
        "winebottlerow",
        sa.Column("id", sqlmodel.AutoString(), nullable=False),
        sa.Column("shelf", sqlmodel.AutoString(), nullable=True),
        sa.Column("category", sqlmodel.AutoString(), nullable=True),
        sa.Column("country", sqlmodel.AutoString(), nullable=True),
        sa.Column("producer", sqlmodel.AutoString(), nullable=True),
        sa.Column("name", sqlmodel.AutoString(), nullable=False),
        sa.Column("vintage", sa.Integer(), nullable=True),
        sa.Column("drink_window_end", sa.Date(), nullable=True),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("purchase_price_nok", sa.Float(), nullable=True),
        sa.Column("region", sqlmodel.AutoString(), nullable=True),
        sa.Column("note", sqlmodel.AutoString(), nullable=True),
        sa.Column("consumed", sa.Boolean(), nullable=False),
        sa.Column("source_row", sa.Integer(), nullable=False),
        sa.Column("source_hash", sqlmodel.AutoString(), nullable=False),
        sa.Column("synced_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "winesyncmeta",
        sa.Column("id", sqlmodel.AutoString(), nullable=False),
        sa.Column("etag", sqlmodel.AutoString(), nullable=False),
        sa.Column("last_sync_at", sa.DateTime(), nullable=True),
        sa.Column("last_attempt_at", sa.DateTime(), nullable=True),
        sa.Column("row_count", sa.Integer(), nullable=False),
        sa.Column("parse_warnings", sqlmodel.AutoString(), nullable=False),
        sa.Column("sync_error", sqlmodel.AutoString(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    if os.environ.get("ALEMBIC_CURRENT_DB", "cache") != "cache":
        return

    op.drop_table("winesyncmeta")
    op.drop_table("winebottlerow")
