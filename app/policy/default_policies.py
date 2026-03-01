"""
Default policy set shipped with HomeAgent.

Policies are matched in order — the first matching enabled policy wins.
High-impact / specific policies must come before broad catch-all entries.
"""
from __future__ import annotations

import json

# Each dict maps directly to ActionPolicy fields (minus id/created_at).
DEFAULT_POLICIES: list[dict[str, object]] = [
    # -----------------------------------------------------------------------
    # High-impact: always confirm
    # -----------------------------------------------------------------------
    {
        "name": "Alarm/security device",
        "tool_pattern": "set_device_capability",
        "arg_conditions": json.dumps({"capability": "alarm_*"}),
        "impact_level": "high",
        "requires_confirm": True,
        "confirm_message": "Modify a security/alarm device?",
    },
    {
        "name": "Door lock/unlock",
        "tool_pattern": "set_device_capability",
        "arg_conditions": json.dumps({"capability": "lock_mode"}),
        "impact_level": "high",
        "requires_confirm": True,
        "confirm_message": "Change door lock state?",
    },
    {
        "name": "Water shutoff",
        "tool_pattern": "set_device_capability",
        "arg_conditions": json.dumps({"capability": "water_*"}),
        "impact_level": "high",
        "requires_confirm": True,
        "confirm_message": "Control water shutoff valve?",
    },
    {
        "name": "Flow trigger",
        "tool_pattern": "trigger_flow",
        "arg_conditions": "{}",
        "impact_level": "high",
        "requires_confirm": True,
        "confirm_message": "Trigger this Homey flow?",
    },
    # -----------------------------------------------------------------------
    # Medium-impact: execute immediately but log prominently
    # -----------------------------------------------------------------------
    {
        "name": "Single device control",
        "tool_pattern": "set_device_capability",
        "arg_conditions": "{}",
        "impact_level": "medium",
        "requires_confirm": False,
        "confirm_message": "",
    },
    # -----------------------------------------------------------------------
    # Low-impact: read-only, always pass
    # -----------------------------------------------------------------------
    {
        "name": "Read device state",
        "tool_pattern": "get_device*",
        "arg_conditions": "{}",
        "impact_level": "low",
        "requires_confirm": False,
        "confirm_message": "",
    },
    {
        "name": "List zones",
        "tool_pattern": "get_zone*",
        "arg_conditions": "{}",
        "impact_level": "low",
        "requires_confirm": False,
        "confirm_message": "",
    },
]
