"""
Prometheus MCP server — entry point.

Run:
    python app/main.py

The server exposes a streamable-HTTP MCP endpoint at:
    http://<host>:<port>/mcp
"""
from __future__ import annotations

import logging

from app.config import get_settings
from app.mcp_server import mcp

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)

if __name__ == "__main__":
    settings = get_settings()
    logger.info(
        "Starting Prometheus MCP server on %s:%d (Prometheus: %s)",
        settings.mcp_host,
        settings.mcp_port,
        settings.prometheus_url,
    )
    mcp.run(
        transport="streamable-http",
        host=settings.mcp_host,
        port=settings.mcp_port,
    )
