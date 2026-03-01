from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from sqlmodel import col, select

from app.db import cache_session
from app.models.cache import DeviceSnapshot

logger = logging.getLogger(__name__)


def upsert_snapshot(
    household_id: str,
    device_id: str,
    capability: str,
    value: object,
    source: str,
) -> None:
    """Write or update a device capability snapshot in cache.db."""
    with cache_session() as session:
        existing = session.exec(
            select(DeviceSnapshot)
            .where(DeviceSnapshot.household_id == household_id)
            .where(DeviceSnapshot.device_id == device_id)
            .where(DeviceSnapshot.capability == capability)
        ).first()

        now = datetime.now(timezone.utc)
        if existing:
            existing.value = json.dumps(value)
            existing.updated_at = now
            existing.source = source
        else:
            session.add(
                DeviceSnapshot(
                    household_id=household_id,
                    device_id=device_id,
                    capability=capability,
                    value=json.dumps(value),
                    updated_at=now,
                    source=source,
                )
            )
        session.commit()


def get_household_snapshots(household_id: str) -> list[DeviceSnapshot]:
    """Return all device snapshots for a household, ordered by device_id then capability."""
    with cache_session() as session:
        return list(
            session.exec(
                select(DeviceSnapshot)
                .where(DeviceSnapshot.household_id == household_id)
                .order_by(
                    col(DeviceSnapshot.device_id),
                    col(DeviceSnapshot.capability),
                )
            ).all()
        )


def format_snapshots_for_prompt(snapshots: list[DeviceSnapshot]) -> str:
    """Format device snapshots as a compact text block for the system prompt."""
    if not snapshots:
        return ""

    # Group by device_id
    devices: dict[str, list[tuple[str, object]]] = {}
    for snap in snapshots:
        devices.setdefault(snap.device_id, []).append(
            (snap.capability, json.loads(snap.value))
        )

    lines = ["## Current Device States"]
    for device_id, caps in devices.items():
        cap_parts = ", ".join(f"{c}={v}" for c, v in caps)
        lines.append(f"- {device_id}: {cap_parts}")
    return "\n".join(lines)


def update_snapshots_from_tool_calls(
    household_id: str,
    messages: list[object],
) -> None:
    """
    Scan new agent messages for Homey tool calls and update device snapshots.

    Looks for ToolCallPart entries with names starting with 'homey_' and
    extracts device_id / capability / value from the tool args.
    """
    from pydantic_ai.messages import ModelResponse, ToolCallPart

    for msg in messages:
        if not isinstance(msg, ModelResponse):
            continue
        for part in msg.parts:
            if not isinstance(part, ToolCallPart):
                continue
            if not part.tool_name.startswith("homey_"):
                continue
            args = part.args if isinstance(part.args, dict) else {}
            _try_update_from_tool(household_id, part.tool_name, args)


def _try_update_from_tool(
    household_id: str, tool_name: str, args: dict[str, object]
) -> None:
    """Best-effort: extract state update from a Homey tool call and persist it."""
    try:
        device_id = str(args.get("device_id", ""))
        capability = str(args.get("capability", ""))
        value = args.get("value")

        if device_id and capability and value is not None:
            upsert_snapshot(household_id, device_id, capability, value, "agent_action")
    except Exception:
        logger.debug("Could not update state cache from tool call %s", tool_name)
