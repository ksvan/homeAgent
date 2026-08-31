"""Add telegramlinkcode table to cache.db

Revision ID: 0012_cache
Revises: 0011_cache
Create Date: 2026-08-31

"""

from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op

revision: str = "0012_cache"
down_revision: Union[str, None] = "0011_cache"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _is_cache(url: str) -> bool:
    return "cache" in url


def upgrade() -> None:
    url = op.get_bind().engine.url.render_as_string(hide_password=False)
    if not _is_cache(url):
        return

    op.create_table(
        "telegramlinkcode",
        sa.Column("id", sqlmodel.AutoString(), nullable=False),
        sa.Column("code_hash", sqlmodel.AutoString(), nullable=False),
        sa.Column("user_id", sqlmodel.AutoString(), nullable=False),
        sa.Column("household_id", sqlmodel.AutoString(), nullable=False),
        sa.Column("created_by_user_id", sqlmodel.AutoString(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("used_at", sa.DateTime(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code_hash", name="uq_telegramlinkcode_code_hash"),
    )
    op.create_index("ix_telegramlinkcode_code_hash", "telegramlinkcode", ["code_hash"])
    op.create_index("ix_telegramlinkcode_user_id", "telegramlinkcode", ["user_id"])
    op.create_index("ix_telegramlinkcode_household_id", "telegramlinkcode", ["household_id"])


def downgrade() -> None:
    url = op.get_bind().engine.url.render_as_string(hide_password=False)
    if not _is_cache(url):
        return
    op.drop_table("telegramlinkcode")
