"""Add email channel tables to cache.db

Revision ID: 0004_cache
Revises: 0003_cache
Create Date: 2026-05-08

"""

import os
from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op

revision: str = "0004_cache"
down_revision: Union[str, None] = "0003_cache"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if os.environ.get("ALEMBIC_CURRENT_DB", "cache") != "cache":
        return

    op.create_table(
        "emailmessage",
        sa.Column("id", sqlmodel.AutoString(), nullable=False),
        sa.Column("provider", sqlmodel.AutoString(), nullable=False, server_default="agentmail"),
        sa.Column("provider_event_id", sqlmodel.AutoString(), nullable=True),
        sa.Column("provider_delivery_id", sqlmodel.AutoString(), nullable=True),
        sa.Column("provider_message_id", sqlmodel.AutoString(), nullable=False),
        sa.Column("provider_thread_id", sqlmodel.AutoString(), nullable=True),
        sa.Column("provider_inbox_id", sqlmodel.AutoString(), nullable=False),
        sa.Column("household_id", sqlmodel.AutoString(), nullable=True),
        sa.Column("user_id", sqlmodel.AutoString(), nullable=True),
        sa.Column("channel_user_id", sqlmodel.AutoString(), nullable=False),
        sa.Column("from_email", sqlmodel.AutoString(), nullable=False),
        sa.Column("to_json", sqlmodel.AutoString(), nullable=False, server_default="[]"),
        sa.Column("cc_json", sqlmodel.AutoString(), nullable=False, server_default="[]"),
        sa.Column("subject", sqlmodel.AutoString(), nullable=False, server_default=""),
        sa.Column("timestamp", sa.DateTime(), nullable=True),
        sa.Column("status", sqlmodel.AutoString(), nullable=False, server_default="RECEIVED"),
        sa.Column("status_reason", sqlmodel.AutoString(), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_attempt_at", sa.DateTime(), nullable=True),
        sa.Column("locked_at", sa.DateTime(), nullable=True),
        sa.Column("last_error", sqlmodel.AutoString(), nullable=True),
        sa.Column("auth_status", sqlmodel.AutoString(), nullable=True),
        sa.Column("auth_details_json", sqlmodel.AutoString(), nullable=True),
        sa.Column("reply_to_email", sqlmodel.AutoString(), nullable=True),
        sa.Column("instruction_text", sqlmodel.AutoString(), nullable=False, server_default=""),
        sa.Column("intake_summary_text", sqlmodel.AutoString(), nullable=False, server_default=""),
        sa.Column("proposed_action_json", sqlmodel.AutoString(), nullable=True),
        sa.Column("confirmation_id", sqlmodel.AutoString(), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(), nullable=True),
        sa.Column("provider_metadata_json", sqlmodel.AutoString(), nullable=False, server_default="{}"),
        sa.Column("raw_debug_json", sqlmodel.AutoString(), nullable=True),
        sa.Column("raw_debug_expires_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("processed_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_emailmessage_provider_event_id", "emailmessage", ["provider_event_id"])
    op.create_index("ix_emailmessage_provider_delivery_id", "emailmessage", ["provider_delivery_id"])
    op.create_index("ix_emailmessage_provider_message_id", "emailmessage", ["provider_message_id"])
    op.create_index("ix_emailmessage_household_id", "emailmessage", ["household_id"])
    op.create_index("ix_emailmessage_user_id", "emailmessage", ["user_id"])
    op.create_index("ix_emailmessage_status", "emailmessage", ["status"])
    op.create_index("ix_emailmessage_next_attempt_at", "emailmessage", ["next_attempt_at"])
    op.create_index(
        "uq_emailmessage_provider_message_id",
        "emailmessage",
        ["provider", "provider_message_id"],
        unique=True,
    )

    op.create_table(
        "emailattachment",
        sa.Column("id", sqlmodel.AutoString(), nullable=False),
        sa.Column("email_message_id", sqlmodel.AutoString(), nullable=False),
        sa.Column("provider_attachment_id", sqlmodel.AutoString(), nullable=False),
        sa.Column("filename", sqlmodel.AutoString(), nullable=False, server_default=""),
        sa.Column("content_type", sqlmodel.AutoString(), nullable=False, server_default=""),
        sa.Column("size", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("inline", sa.Boolean(), nullable=False, server_default="0"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_emailattachment_email_message_id", "emailattachment", ["email_message_id"])

    op.create_table(
        "emailintakeconfirmation",
        sa.Column("token", sqlmodel.AutoString(), nullable=False),
        sa.Column("email_message_id", sqlmodel.AutoString(), nullable=False),
        sa.Column("user_id", sqlmodel.AutoString(), nullable=False),
        sa.Column("household_id", sqlmodel.AutoString(), nullable=False),
        sa.Column("intake_text", sqlmodel.AutoString(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("token"),
    )
    op.create_index(
        "ix_emailintakeconfirmation_email_message_id",
        "emailintakeconfirmation",
        ["email_message_id"],
    )


def downgrade() -> None:
    if os.environ.get("ALEMBIC_CURRENT_DB", "cache") != "cache":
        return
    op.drop_table("emailintakeconfirmation")
    op.drop_table("emailattachment")
    op.drop_table("emailmessage")
