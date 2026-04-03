from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Field, SQLModel


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _uuid() -> str:
    return str(uuid.uuid4())


class EventRule(SQLModel, table=True):
    """
    Defines when an inbound event (e.g. Homey device state change) should
    wake the agent vs just updating the state cache silently.

    Lives in users.db alongside Task and ScheduledPrompt — durable household
    operating configuration.
    """

    id: str = Field(default_factory=_uuid, primary_key=True)
    household_id: str = Field(index=True)
    user_id: str = Field(foreign_key="user.id", index=True)
    channel_user_id: str  # channel-specific target ID (e.g. str(telegram_id))
    name: str  # human label, e.g. "Motion alert after 22:00"

    # --- Matching ---
    source: str = "homey"           # "homey" | future: "calendar", "internal"
    event_type: str = "*"           # "device_state_change" | "flow_trigger" | "*"
    entity_id: str = "*"            # specific device UUID, or "*" for any
    capability: Optional[str] = None  # filter by capability name, nullable = any
    # JSON object: {"eq": true}, {"gt": 22.5}, {"ne": null} — applied to payload value
    value_filter_json: Optional[str] = None

    # --- Delivery conditions ---
    # JSON object: {"quiet_hours_start": "22:00", "quiet_hours_end": "07:00",
    #               "days_of_week": [0,1,2,3,4]}
    condition_json: Optional[str] = None
    cooldown_minutes: int = 5  # minimum minutes between triggers for this rule

    # --- Agent prompt ---
    # Template passed to agent_run(); supports {entity_id}, {entity_name},
    # {capability}, {value}, {zone}, {time} interpolation
    prompt_template: str

    # --- Control loop ---
    # "notify_only" = wake agent once (Phase 2 behaviour, default)
    # "task_loop"   = resolve or create a durable control Task; keep events correlated
    run_mode: str = "notify_only"
    # Task kind when creating a control task; None = "track"
    task_kind_default: Optional[str] = None
    # Correlation key template; None = "rule:{rule_id}:entity:{entity_id}"
    correlation_key_tpl: Optional[str] = None

    enabled: bool = True
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)

    # Persisted cooldown timestamp — survives restarts.
    # The dispatcher also caches this in-memory for fast checks.
    last_triggered_at: Optional[datetime] = None
