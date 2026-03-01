"""Initial schema for memory.db

Revision ID: 0001_memory
Revises:
Create Date: 2026-03-01

"""

from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op

revision: str = "0001_memory"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = "memory_db"
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    import os

    if os.environ.get("ALEMBIC_CURRENT_DB", "memory") != "memory":
        return

    op.create_table(
        "userprofile",
        sa.Column("id", sqlmodel.AutoString(), nullable=False),
        sa.Column("user_id", sqlmodel.AutoString(), nullable=False),
        sa.Column("summary", sqlmodel.AutoString(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id"),
    )
    op.create_index("ix_userprofile_user_id", "userprofile", ["user_id"], unique=True)

    op.create_table(
        "householdprofile",
        sa.Column("id", sqlmodel.AutoString(), nullable=False),
        sa.Column("household_id", sqlmodel.AutoString(), nullable=False),
        sa.Column("summary", sqlmodel.AutoString(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("household_id"),
    )
    op.create_index(
        "ix_householdprofile_household_id", "householdprofile", ["household_id"], unique=True
    )

    op.create_table(
        "episodicmemory",
        sa.Column("id", sqlmodel.AutoString(), nullable=False),
        sa.Column("household_id", sqlmodel.AutoString(), nullable=False),
        sa.Column("user_id", sqlmodel.AutoString(), nullable=True),
        sa.Column("content", sqlmodel.AutoString(), nullable=False),
        sa.Column("embedding_id", sqlmodel.AutoString(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("source_run_id", sqlmodel.AutoString(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_episodicmemory_household_id", "episodicmemory", ["household_id"])
    op.create_index("ix_episodicmemory_user_id", "episodicmemory", ["user_id"])

    op.create_table(
        "conversationmessage",
        sa.Column("id", sqlmodel.AutoString(), nullable=False),
        sa.Column("user_id", sqlmodel.AutoString(), nullable=False),
        sa.Column("role", sqlmodel.AutoString(), nullable=False),
        sa.Column("content", sqlmodel.AutoString(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_conversationmessage_user_id", "conversationmessage", ["user_id"])

    op.create_table(
        "conversationsummary",
        sa.Column("id", sqlmodel.AutoString(), nullable=False),
        sa.Column("user_id", sqlmodel.AutoString(), nullable=False),
        sa.Column("summary", sqlmodel.AutoString(), nullable=False),
        sa.Column("covers_through_message_id", sqlmodel.AutoString(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id"),
    )
    op.create_index(
        "ix_conversationsummary_user_id", "conversationsummary", ["user_id"], unique=True
    )


def downgrade() -> None:
    import os

    if os.environ.get("ALEMBIC_CURRENT_DB", "memory") != "memory":
        return

    op.drop_table("conversationsummary")
    op.drop_table("conversationmessage")
    op.drop_table("episodicmemory")
    op.drop_table("householdprofile")
    op.drop_table("userprofile")
