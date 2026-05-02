"""Add flight monitor tables to cache.db

Revision ID: 0003_cache
Revises: 0002_cache
Create Date: 2026-05-02

"""

import os
from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op

revision: str = "0003_cache"
down_revision: Union[str, None] = "0002_cache"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if os.environ.get("ALEMBIC_CURRENT_DB", "cache") != "cache":
        return

    op.create_table(
        "flightwatch",
        sa.Column("id", sqlmodel.AutoString(), nullable=False),
        sa.Column("household_id", sqlmodel.AutoString(), nullable=False),
        sa.Column("user_id", sqlmodel.AutoString(), nullable=False),
        sa.Column("channel_user_id", sqlmodel.AutoString(), nullable=False),
        sa.Column("label", sqlmodel.AutoString(), nullable=True),
        sa.Column("carrier_code", sqlmodel.AutoString(), nullable=False),
        sa.Column("flight_number", sqlmodel.AutoString(), nullable=False),
        sa.Column("scheduled_departure_date", sa.Date(), nullable=False),
        sa.Column("origin", sqlmodel.AutoString(), nullable=True),
        sa.Column("destination", sqlmodel.AutoString(), nullable=True),
        sa.Column("operating_carrier_code", sqlmodel.AutoString(), nullable=True),
        sa.Column("marketing_carrier_code", sqlmodel.AutoString(), nullable=True),
        sa.Column("codeshares_json", sqlmodel.AutoString(), nullable=False),
        sa.Column("aircraft_type", sqlmodel.AutoString(), nullable=True),
        sa.Column("tail_number", sqlmodel.AutoString(), nullable=True),
        sa.Column("status", sqlmodel.AutoString(), nullable=False),
        sa.Column("status_reason", sqlmodel.AutoString(), nullable=True),
        sa.Column("monitoring_starts_at", sa.DateTime(), nullable=True),
        sa.Column("monitoring_ends_at", sa.DateTime(), nullable=True),
        sa.Column("provider", sqlmodel.AutoString(), nullable=False),
        sa.Column("provider_flight_id", sqlmodel.AutoString(), nullable=True),
        sa.Column("provider_alert_id", sqlmodel.AutoString(), nullable=True),
        sa.Column("provider_subscription_kind", sqlmodel.AutoString(), nullable=True),
        sa.Column("webhook_token_hash", sqlmodel.AutoString(), nullable=True),
        sa.Column("consecutive_provider_errors", sa.Integer(), nullable=False),
        sa.Column("notify_policy_json", sqlmodel.AutoString(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_flightwatch_user_id", "flightwatch", ["user_id"])
    op.create_index("ix_flightwatch_household_id", "flightwatch", ["household_id"])
    op.create_index("ix_flightwatch_status", "flightwatch", ["status"])
    op.create_index("ix_flightwatch_webhook_token_hash", "flightwatch", ["webhook_token_hash"])

    op.create_table(
        "flightstatussnapshot",
        sa.Column("id", sqlmodel.AutoString(), nullable=False),
        sa.Column("watch_id", sqlmodel.AutoString(), nullable=False),
        sa.Column("provider", sqlmodel.AutoString(), nullable=False),
        sa.Column("provider_updated_at", sa.DateTime(), nullable=True),
        sa.Column("fetched_at", sa.DateTime(), nullable=False),
        sa.Column("state", sqlmodel.AutoString(), nullable=False),
        sa.Column("scheduled_out", sa.DateTime(), nullable=True),
        sa.Column("estimated_out", sa.DateTime(), nullable=True),
        sa.Column("actual_out", sa.DateTime(), nullable=True),
        sa.Column("scheduled_off", sa.DateTime(), nullable=True),
        sa.Column("estimated_off", sa.DateTime(), nullable=True),
        sa.Column("actual_off", sa.DateTime(), nullable=True),
        sa.Column("scheduled_on", sa.DateTime(), nullable=True),
        sa.Column("estimated_on", sa.DateTime(), nullable=True),
        sa.Column("actual_on", sa.DateTime(), nullable=True),
        sa.Column("scheduled_in", sa.DateTime(), nullable=True),
        sa.Column("estimated_in", sa.DateTime(), nullable=True),
        sa.Column("actual_in", sa.DateTime(), nullable=True),
        sa.Column("departure_terminal", sqlmodel.AutoString(), nullable=True),
        sa.Column("departure_gate", sqlmodel.AutoString(), nullable=True),
        sa.Column("arrival_terminal", sqlmodel.AutoString(), nullable=True),
        sa.Column("arrival_gate", sqlmodel.AutoString(), nullable=True),
        sa.Column("baggage_claim", sqlmodel.AutoString(), nullable=True),
        sa.Column("delay_minutes", sa.Integer(), nullable=True),
        sa.Column("cancelled", sa.Boolean(), nullable=False),
        sa.Column("diverted", sa.Boolean(), nullable=False),
        sa.Column("diversion_airport", sqlmodel.AutoString(), nullable=True),
        sa.Column("raw_json", sqlmodel.AutoString(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_flightstatussnapshot_watch_id", "flightstatussnapshot", ["watch_id"])
    op.create_index("ix_flightstatussnapshot_fetched_at", "flightstatussnapshot", ["fetched_at"])

    op.create_table(
        "flightevent",
        sa.Column("id", sqlmodel.AutoString(), nullable=False),
        sa.Column("watch_id", sqlmodel.AutoString(), nullable=True),
        sa.Column("provider", sqlmodel.AutoString(), nullable=False),
        sa.Column("provider_event_id", sqlmodel.AutoString(), nullable=True),
        sa.Column("event_hash", sqlmodel.AutoString(), nullable=False),
        sa.Column("event_type", sqlmodel.AutoString(), nullable=False),
        sa.Column("severity", sqlmodel.AutoString(), nullable=False),
        sa.Column("received_at", sa.DateTime(), nullable=False),
        sa.Column("provider_timestamp", sa.DateTime(), nullable=True),
        sa.Column("raw_json", sqlmodel.AutoString(), nullable=False),
        sa.Column("normalized_json", sqlmodel.AutoString(), nullable=False),
        sa.Column("processed", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_flightevent_watch_id", "flightevent", ["watch_id"])
    op.create_index("ix_flightevent_event_hash", "flightevent", ["event_hash"])
    op.create_index("ix_flightevent_received_at", "flightevent", ["received_at"])


def downgrade() -> None:
    if os.environ.get("ALEMBIC_CURRENT_DB", "cache") != "cache":
        return

    op.drop_table("flightevent")
    op.drop_table("flightstatussnapshot")
    op.drop_table("flightwatch")
