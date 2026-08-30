"""Harden webchatsession table in cache.db (hashed token, absolute expiry, revoke)

Revision ID: 0010_cache
Revises: 0009_cache
Create Date: 2026-08-30

Replaces the plaintext `token` primary key with a hashed `token_hash`, adds
`absolute_expires_at` (fixed at creation, never slides) and `revoked_at`
(explicit logout/admin-revoke, kept for audit rather than deleted) — see
docs/household-identity-and-access-design.md Option D / Goal 7.

The table holds only ephemeral session state (cache.db, "operational
runtime state" per CLAUDE.md) — dropping and recreating it is safe and
simpler than an in-place column/PK migration on SQLite. Any session active
at deploy time is invalidated; the affected household member just picks
themselves again (or, once WebAuthn login ships, re-authenticates).
"""

from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op

revision: str = "0010_cache"
down_revision: Union[str, None] = "0009_cache"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _is_cache(url: str) -> bool:
    return "cache" in url


def upgrade() -> None:
    url = op.get_bind().engine.url.render_as_string(hide_password=False)
    if not _is_cache(url):
        return

    op.drop_table("webchatsession")
    op.create_table(
        "webchatsession",
        sa.Column("id", sqlmodel.AutoString(), nullable=False),
        sa.Column("token_hash", sqlmodel.AutoString(), nullable=False),
        sa.Column("user_id", sqlmodel.AutoString(), nullable=False),
        sa.Column("household_id", sqlmodel.AutoString(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("absolute_expires_at", sa.DateTime(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash", name="uq_webchatsession_token_hash"),
    )
    op.create_index("ix_webchatsession_token_hash", "webchatsession", ["token_hash"])
    op.create_index("ix_webchatsession_user_id", "webchatsession", ["user_id"])
    op.create_index("ix_webchatsession_household_id", "webchatsession", ["household_id"])


def downgrade() -> None:
    url = op.get_bind().engine.url.render_as_string(hide_password=False)
    if not _is_cache(url):
        return
    op.drop_table("webchatsession")
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
