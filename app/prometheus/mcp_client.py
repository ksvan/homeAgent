from __future__ import annotations

import logging

from pydantic_ai.mcp import MCPServerStreamableHTTP

from app.config import get_settings

logger = logging.getLogger(__name__)

_mcp_server: MCPServerStreamableHTTP | None = None


def get_mcp_server() -> MCPServerStreamableHTTP | None:
    """Return the running Prometheus MCP server instance, or None if not configured."""
    return _mcp_server


def _create_mcp_server() -> MCPServerStreamableHTTP | None:
    settings = get_settings()
    if not settings.prometheus_mcp_url:
        logger.info("Prometheus MCP not configured — metrics tools disabled")
        return None
    return MCPServerStreamableHTTP(
        url=settings.prometheus_mcp_url,
    )


async def start_mcp() -> MCPServerStreamableHTTP | None:
    """Connect to the Prometheus MCP server. Called during FastAPI lifespan startup."""
    global _mcp_server
    server = _create_mcp_server()
    if server is None:
        return None
    try:
        await server.__aenter__()
        _mcp_server = server
        logger.info("Prometheus MCP connection established (%s)", get_settings().prometheus_mcp_url)
        return server
    except Exception:
        logger.exception("Failed to connect to Prometheus MCP — metrics tools disabled")
        return None


async def stop_mcp() -> None:
    """Disconnect from the Prometheus MCP server. Called during FastAPI lifespan shutdown."""
    global _mcp_server
    if _mcp_server is not None:
        try:
            await _mcp_server.__aexit__(None, None, None)
        except Exception:
            logger.warning("Error during Prometheus MCP shutdown", exc_info=True)
        finally:
            _mcp_server = None
            logger.info("Prometheus MCP connection closed")
