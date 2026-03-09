from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def refresh_home_profile(household_id: str) -> None:
    """
    Query the Homey MCP server for zones and devices, then write a structured
    summary to the household profile in memory.db.

    This is called once at startup (after the MCP connection is established)
    and can be triggered again by the agent via a 'refresh home profile' tool.
    """
    from app.homey.mcp_client import get_mcp_server
    from app.memory.profiles import upsert_household_profile

    server = get_mcp_server()
    if server is None:
        logger.debug("Homey MCP not available — skipping home profile refresh")
        return

    # List all available tools and store as a summary in the household profile
    try:
        tools = await server.list_tools()
        tool_summaries = [
            {"name": t.name, "description": (t.description or "")[:120]}
            for t in tools
        ]
        await _try_discover_devices(server, household_id)

        upsert_household_profile(
            household_id,
            {
                "homey_tools_count": len(tool_summaries),
                "homey_tools": tool_summaries,
            },
        )
        logger.info(
            "Home profile refreshed: %d Homey tools registered", len(tool_summaries)
        )
    except Exception:
        logger.warning("Home profile refresh failed", exc_info=True)


async def _try_discover_devices(server: object, household_id: str) -> None:
    """
    Call get_home_structure to populate the household profile with zones, devices,
    and moods.  Fails silently if the tool isn't available.
    """
    from pydantic_ai.mcp import MCPServerStreamableHTTP

    from app.memory.profiles import upsert_household_profile

    if not isinstance(server, MCPServerStreamableHTTP):
        return

    try:
        result = await server.direct_call_tool("get_home_structure", {}, None)
        if result:
            upsert_household_profile(household_id, {"homey_home_structure": str(result)[:4000]})
            logger.info("Discovered Homey home structure")
    except Exception:
        logger.debug("Could not fetch home structure from Homey MCP", exc_info=True)
