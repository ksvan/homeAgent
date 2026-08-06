"""Add webchatsession table to cache.db

Revision ID: 0006_cache
Revises: 0005_cache
Create Date: 2026-08-06

"""

from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op

revision: str = "0006_cache"
down_revision: Union[str, None] = "0005_cache"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _is_cache(url: str) -> bool:
    return "cache" in url


def upgrade() -> None:
    url = op.get_bind().engine.url.render_as_string(hide_password=False)
    if not _is_cache(url):
        return
    op.create_table(
        "webchatsession",
        sa.Column("token", sqlmodel.AutoString(), nullable=False),
        sa.Column("user_id", sqlmodel.AutoString(), nullable=False),
        sa.Column("household_id", sqlmodel.AutoString(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("token"),
    )
    op.create_index("ix_webchatsession_user_id", "webchatsession", ["user_id"])
    op.create_index("ix_webchatsession_household_id", "webchatsession", ["household_id"])


def downgrade() -> None:
    url = op.get_bind().engine.url.render_as_string(hide_password=False)
    if not _is_cache(url):
        return
    op.drop_table("webchatsession")
