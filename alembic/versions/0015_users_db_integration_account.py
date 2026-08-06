"""Add integrationaccount table to users.db

Revision ID: 0015_users
Revises: 0014_users
Create Date: 2026-08-06

"""

from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op

revision: str = "0015_users"
down_revision: Union[str, None] = "0014_users"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _is_users(url: str) -> bool:
    return "users" in url


def upgrade() -> None:
    url = op.get_bind().engine.url.render_as_string(hide_password=False)
    if not _is_users(url):
        return
    op.create_table(
        "integrationaccount",
        sa.Column("id", sqlmodel.AutoString(), nullable=False),
        sa.Column("household_id", sqlmodel.AutoString(), nullable=False),
        sa.Column("provider", sqlmodel.AutoString(), nullable=False),
        sa.Column("access_token", sqlmodel.AutoString(), nullable=False),
        sa.Column("refresh_token", sqlmodel.AutoString(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("client_id", sqlmodel.AutoString(), nullable=False),
        sa.Column("client_secret", sqlmodel.AutoString(), nullable=False),
        sa.Column("connected_by_user_id", sqlmodel.AutoString(), nullable=False),
        sa.Column("connected_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["household_id"], ["household.id"]),
        sa.ForeignKeyConstraint(["connected_by_user_id"], ["user.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "household_id", "provider", name="uq_integrationaccount_household_provider"
        ),
    )
    op.create_index(
        "ix_integrationaccount_household_id", "integrationaccount", ["household_id"]
    )


def downgrade() -> None:
    url = op.get_bind().engine.url.render_as_string(hide_password=False)
    if not _is_users(url):
        return
    op.drop_table("integrationaccount")
