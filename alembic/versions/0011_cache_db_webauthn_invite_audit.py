"""Add webauthnchallenge, webchatinvite, and auditlog tables to cache.db

Revision ID: 0011_cache
Revises: 0010_cache
Create Date: 2026-08-30

"""

from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op

revision: str = "0011_cache"
down_revision: Union[str, None] = "0010_cache"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _is_cache(url: str) -> bool:
    return "cache" in url


def upgrade() -> None:
    url = op.get_bind().engine.url.render_as_string(hide_password=False)
    if not _is_cache(url):
        return

    op.create_table(
        "webauthnchallenge",
        sa.Column("id", sqlmodel.AutoString(), nullable=False),
        sa.Column("purpose", sqlmodel.AutoString(), nullable=False),
        sa.Column("challenge", sqlmodel.AutoString(), nullable=False),
        sa.Column("user_id", sqlmodel.AutoString(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("used_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "webchatinvite",
        sa.Column("id", sqlmodel.AutoString(), nullable=False),
        sa.Column("token_hash", sqlmodel.AutoString(), nullable=False),
        sa.Column("user_id", sqlmodel.AutoString(), nullable=False),
        sa.Column("household_id", sqlmodel.AutoString(), nullable=False),
        sa.Column("created_by_user_id", sqlmodel.AutoString(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("used_at", sa.DateTime(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash", name="uq_webchatinvite_token_hash"),
    )
    op.create_index("ix_webchatinvite_token_hash", "webchatinvite", ["token_hash"])
    op.create_index("ix_webchatinvite_user_id", "webchatinvite", ["user_id"])
    op.create_index("ix_webchatinvite_household_id", "webchatinvite", ["household_id"])

    op.create_table(
        "auditlog",
        sa.Column("id", sqlmodel.AutoString(), nullable=False),
        sa.Column("event_type", sqlmodel.AutoString(), nullable=False),
        sa.Column("household_id", sqlmodel.AutoString(), nullable=False),
        sa.Column("actor_user_id", sqlmodel.AutoString(), nullable=True),
        sa.Column("target_user_id", sqlmodel.AutoString(), nullable=True),
        sa.Column("detail", sqlmodel.AutoString(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_auditlog_event_type", "auditlog", ["event_type"])
    op.create_index("ix_auditlog_household_id", "auditlog", ["household_id"])
    op.create_index("ix_auditlog_actor_user_id", "auditlog", ["actor_user_id"])
    op.create_index("ix_auditlog_target_user_id", "auditlog", ["target_user_id"])
    op.create_index("ix_auditlog_created_at", "auditlog", ["created_at"])


def downgrade() -> None:
    url = op.get_bind().engine.url.render_as_string(hide_password=False)
    if not _is_cache(url):
        return
    op.drop_table("auditlog")
    op.drop_table("webchatinvite")
    op.drop_table("webauthnchallenge")
