"""
Tools MCP server — entry point.

Run:
    python app/main.py

Exposes a streamable-HTTP MCP endpoint at:
    http://<host>:<port>/mcp
"""
from __future__ import annotations

import logging

from app.config import get_settings
from app.mcp_server import mcp, register_tools

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)

if __name__ == "__main__":
    settings = get_settings()

    enabled = [
        name
        for name, flag in [
            ("bash", settings.feature_bash),
            ("python", settings.feature_python),
            ("scrape", settings.feature_scrape),
            ("search", settings.feature_search),
            ("sharepoint", settings.feature_sharepoint),
        ]
        if flag
    ]
    logger.info(
        "Starting Tools MCP server on %s:%d  enabled=%s",
        settings.mcp_host,
        settings.mcp_port,
        enabled or ["none"],
    )

    register_tools(settings)

    mcp.run(
        transport="streamable-http",
        host=settings.mcp_host,
        port=settings.mcp_port,
    )
