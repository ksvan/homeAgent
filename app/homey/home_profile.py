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
    Attempt to call Homey discovery tools (get_zones, get_devices) and store
    the results in the household profile.  Fails silently if tools aren't found.
    """
    from pydantic_ai.mcp import MCPServerHTTP

    from app.memory.profiles import upsert_household_profile

    if not isinstance(server, MCPServerHTTP):
        return

    # Try to get zones — Homey uses 'get_zones' or similar
    for zone_tool in ("homey_get_zones", "get_zones"):
        try:
            result = await server.direct_call_tool(zone_tool, {}, None)
            if result:
                upsert_household_profile(household_id, {"homey_zones_raw": str(result)[:2000]})
                logger.info("Discovered Homey zones via %s", zone_tool)
            break
        except Exception:
            continue

    # Try to get devices
    for device_tool in ("homey_get_devices", "get_devices"):
        try:
            result = await server.direct_call_tool(device_tool, {}, None)
            if result:
                upsert_household_profile(household_id, {"homey_devices_raw": str(result)[:2000]})
                logger.info("Discovered Homey devices via %s", device_tool)
            break
        except Exception:
            continue
