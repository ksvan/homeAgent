"""
Default policy set shipped with HomeAgent.

Policies are matched in order — the first matching enabled policy wins.
High-impact / specific policies must come before broad catch-all entries.
"""
from __future__ import annotations

# Each dict maps directly to ActionPolicy fields (minus id/created_at).
DEFAULT_POLICIES: list[dict[str, object]] = [
    # -----------------------------------------------------------------------
    # Homey AI Chat Control (meta-tool pattern: search_tools + use_tool)
    # -----------------------------------------------------------------------
    {
        "name": "Homey use_tool",
        "tool_pattern": "use_tool",
        "arg_conditions": "{}",
        "impact_level": "low",
        "requires_confirm": False,
        "confirm_message": "",
    },
    {
        "name": "Homey search_tools (read-only)",
        "tool_pattern": "search_tools",
        "arg_conditions": "{}",
        "impact_level": "low",
        "requires_confirm": False,
        "confirm_message": "",
    },
]
