"""Make user.telegram_id nullable, add per-surface access flags to users.db

Revision ID: 0017_users
Revises: 0016_users
Create Date: 2026-08-31

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0017_users"
down_revision: Union[str, None] = "0016_users"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _is_users(url: str) -> bool:
    return "users" in url


def upgrade() -> None:
    url = op.get_bind().engine.url.render_as_string(hide_password=False)
    if not _is_users(url):
        return

    with op.batch_alter_table("user") as batch_op:
        batch_op.alter_column("telegram_id", existing_type=sa.Integer(), nullable=True)
        batch_op.add_column(
            sa.Column("telegram_enabled", sa.Boolean(), nullable=False, server_default=sa.true())
        )
        batch_op.add_column(
            sa.Column("web_chat_enabled", sa.Boolean(), nullable=False, server_default=sa.true())
        )


def downgrade() -> None:
    url = op.get_bind().engine.url.render_as_string(hide_password=False)
    if not _is_users(url):
        return

    with op.batch_alter_table("user") as batch_op:
        batch_op.drop_column("web_chat_enabled")
        batch_op.drop_column("telegram_enabled")
        batch_op.alter_column("telegram_id", existing_type=sa.Integer(), nullable=False)
