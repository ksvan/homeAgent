"""Initial schema for cache.db

Revision ID: 0001_cache
Revises:
Create Date: 2026-03-01

"""

from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op

revision: str = "0001_cache"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = "cache_db"
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    import os

    if os.environ.get("ALEMBIC_CURRENT_DB", "cache") != "cache":
        return

    op.create_table(
        "devicesnapshot",
        sa.Column("id", sqlmodel.AutoString(), nullable=False),
        sa.Column("household_id", sqlmodel.AutoString(), nullable=False),
        sa.Column("device_id", sqlmodel.AutoString(), nullable=False),
        sa.Column("capability", sqlmodel.AutoString(), nullable=False),
        sa.Column("value", sqlmodel.AutoString(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("source", sqlmodel.AutoString(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_devicesnapshot_household_id", "devicesnapshot", ["household_id"])
    op.create_index("ix_devicesnapshot_device_id", "devicesnapshot", ["device_id"])

    op.create_table(
        "eventlog",
        sa.Column("id", sqlmodel.AutoString(), nullable=False),
        sa.Column("event_type", sqlmodel.AutoString(), nullable=False),
        sa.Column("household_id", sqlmodel.AutoString(), nullable=False),
        sa.Column("user_id", sqlmodel.AutoString(), nullable=True),
        sa.Column("payload", sqlmodel.AutoString(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_eventlog_household_id", "eventlog", ["household_id"])
    op.create_index("ix_eventlog_user_id", "eventlog", ["user_id"])

    op.create_table(
        "agentrunlog",
        sa.Column("id", sqlmodel.AutoString(), nullable=False),
        sa.Column("household_id", sqlmodel.AutoString(), nullable=False),
        sa.Column("user_id", sqlmodel.AutoString(), nullable=False),
        sa.Column("trigger_event_id", sqlmodel.AutoString(), nullable=True),
        sa.Column("model_used", sqlmodel.AutoString(), nullable=False),
        sa.Column("input_summary", sqlmodel.AutoString(), nullable=False),
        sa.Column("tools_called", sqlmodel.AutoString(), nullable=False),
        sa.Column("output_summary", sqlmodel.AutoString(), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("tokens_used", sqlmodel.AutoString(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_agentrunlog_household_id", "agentrunlog", ["household_id"])
    op.create_index("ix_agentrunlog_user_id", "agentrunlog", ["user_id"])
    op.create_index("ix_agentrunlog_trigger_event_id", "agentrunlog", ["trigger_event_id"])

    op.create_table(
        "pendingaction",
        sa.Column("token", sqlmodel.AutoString(), nullable=False),
        sa.Column("household_id", sqlmodel.AutoString(), nullable=False),
        sa.Column("user_id", sqlmodel.AutoString(), nullable=False),
        sa.Column("tool_name", sqlmodel.AutoString(), nullable=False),
        sa.Column("tool_args", sqlmodel.AutoString(), nullable=False),
        sa.Column("policy_name", sqlmodel.AutoString(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("token"),
    )
    op.create_index("ix_pendingaction_household_id", "pendingaction", ["household_id"])
    op.create_index("ix_pendingaction_user_id", "pendingaction", ["user_id"])


def downgrade() -> None:
    import os

    if os.environ.get("ALEMBIC_CURRENT_DB", "cache") != "cache":
        return

    op.drop_table("pendingaction")
    op.drop_table("agentrunlog")
    op.drop_table("eventlog")
    op.drop_table("devicesnapshot")
