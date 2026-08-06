"""Add oauthstate table to cache.db

Revision ID: 0007_cache
Revises: 0006_cache
Create Date: 2026-08-06

"""

from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op

revision: str = "0007_cache"
down_revision: Union[str, None] = "0006_cache"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _is_cache(url: str) -> bool:
    return "cache" in url


def upgrade() -> None:
    url = op.get_bind().engine.url.render_as_string(hide_password=False)
    if not _is_cache(url):
        return
    op.create_table(
        "oauthstate",
        sa.Column("state", sqlmodel.AutoString(), nullable=False),
        sa.Column("provider", sqlmodel.AutoString(), nullable=False),
        sa.Column("household_id", sqlmodel.AutoString(), nullable=False),
        sa.Column("initiating_user_id", sqlmodel.AutoString(), nullable=False),
        sa.Column("pkce_verifier", sqlmodel.AutoString(), nullable=False),
        sa.Column("redirect_uri", sqlmodel.AutoString(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("state"),
    )
    op.create_index("ix_oauthstate_household_id", "oauthstate", ["household_id"])


def downgrade() -> None:
    url = op.get_bind().engine.url.render_as_string(hide_password=False)
    if not _is_cache(url):
        return
    op.drop_table("oauthstate")
