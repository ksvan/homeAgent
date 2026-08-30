"""Add webauthncredential table and user.is_active to users.db

Revision ID: 0016_users
Revises: 0015_users
Create Date: 2026-08-30

"""

from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op

revision: str = "0016_users"
down_revision: Union[str, None] = "0015_users"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _is_users(url: str) -> bool:
    return "users" in url


def upgrade() -> None:
    url = op.get_bind().engine.url.render_as_string(hide_password=False)
    if not _is_users(url):
        return

    op.add_column(
        "user",
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
    )

    op.create_table(
        "webauthncredential",
        sa.Column("id", sqlmodel.AutoString(), nullable=False),
        sa.Column("user_id", sqlmodel.AutoString(), nullable=False),
        sa.Column("credential_id", sqlmodel.AutoString(), nullable=False),
        sa.Column("public_key", sqlmodel.AutoString(), nullable=False),
        sa.Column("sign_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("device_label", sqlmodel.AutoString(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("last_used_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("credential_id", name="uq_webauthncredential_credential_id"),
    )
    op.create_index("ix_webauthncredential_user_id", "webauthncredential", ["user_id"])
    op.create_index(
        "ix_webauthncredential_credential_id", "webauthncredential", ["credential_id"]
    )


def downgrade() -> None:
    url = op.get_bind().engine.url.render_as_string(hide_password=False)
    if not _is_users(url):
        return
    op.drop_table("webauthncredential")
    with op.batch_alter_table("user") as batch_op:
        batch_op.drop_column("is_active")
