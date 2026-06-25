"""Add fetch_source to flightstatussnapshot

Revision ID: 0005_cache
Revises: 0004_cache
Create Date: 2026-06-25

"""

import os
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005_cache"
down_revision: Union[str, None] = "0004_cache"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _is_cache(url: str) -> bool:
    return "cache" in url


def upgrade() -> None:
    url = op.get_bind().engine.url.render_as_string(hide_password=False)
    if not _is_cache(url):
        return
    op.add_column(
        "flightstatussnapshot",
        sa.Column("fetch_source", sa.String(), nullable=False, server_default="poll"),
    )


def downgrade() -> None:
    url = op.get_bind().engine.url.render_as_string(hide_password=False)
    if not _is_cache(url):
        return
    op.drop_column("flightstatussnapshot", "fetch_source")
