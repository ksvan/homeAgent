"""
Default policy set shipped with HomeAgent.

Policies are matched in order — the first matching enabled policy wins.
High-impact / specific policies must come before broad catch-all entries.
"""

from __future__ import annotations

# Oda tools with no real-world side effect — read-only catalog/recipe/order
# lookups, plus `feedback` (sends commentary about the MCP tools themselves,
# not store/delivery/complaints). None of these can place an order or spend
# money — Oda's MCP has no checkout/payment tool at all. Listed explicitly
# rather than relying on the gate's get_/list_/search_ prefix fallback,
# since several of these (similar_and_related_products, likely_to_buy,
# unique_for_you, order_tracking, recipe_search, feedback) don't match that
# prefix. See docs/oda-grocery-mcp-tool-design.md "Policy gate additions".
_ODA_AUTO_ALLOW_TOOLS: list[str] = [
    "get_cart",
    "get_delivery_addresses",
    "get_delivery_slots",
    "product_search",
    "get_category",
    "get_brand",
    "similar_and_related_products",
    "likely_to_buy",
    "unique_for_you",
    "recipe_search",
    "get_liked_recipes",
    "get_purchased_recipes",
    "get_product_lists",
    "get_dinner_lists",
    "get_product_list",
    "get_orders",
    "get_order",
    "order_tracking",
    "feedback",
]

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
    # -----------------------------------------------------------------------
    # Oda grocery MCP. manipulate_cart / select_delivery_slot are the only
    # tools with a real side effect — both reversible and neither spends
    # money (no checkout/payment tool exists), but both mutate a real shared
    # household resource, so they require confirmation rather than being
    # auto-allowed like the rest. See docs/oda-grocery-mcp-tool-design.md
    # "Policy gate additions".
    # -----------------------------------------------------------------------
    *[
        {
            "name": f"Oda {tool} (read-only / no side effect)",
            "tool_pattern": tool,
            "arg_conditions": "{}",
            "impact_level": "low",
            "requires_confirm": False,
            "confirm_message": "",
        }
        for tool in _ODA_AUTO_ALLOW_TOOLS
    ],
    {
        "name": "Oda manipulate_cart",
        "tool_pattern": "manipulate_cart",
        "arg_conditions": "{}",
        "impact_level": "medium",
        "requires_confirm": True,
        # Built dynamically per call by gate._build_manipulate_cart_message
        # (operation count/direction) — see gate.py's _DYNAMIC_CONFIRM_MESSAGE_TOOLS.
        # This static value is never read; empty matches the same convention
        # already used for Homey's use_tool row above.
        "confirm_message": "",
    },
    {
        "name": "Oda select_delivery_slot",
        "tool_pattern": "select_delivery_slot",
        "arg_conditions": "{}",
        "impact_level": "medium",
        "requires_confirm": True,
        "confirm_message": "Book this Oda delivery slot?",
    },
]
