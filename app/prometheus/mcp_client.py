from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from pydantic_ai import RunContext
from pydantic_ai.mcp import MCPServerStreamableHTTP

from app.config import get_settings

logger = logging.getLogger(__name__)

_DirectCallFn = Callable[[str, dict[str, Any], Any], Awaitable[Any]]


async def _instrument_process_tool_call(
    ctx: RunContext[Any],
    call_tool: _DirectCallFn,
    tool_name: str,
    tool_args: dict[str, Any],
) -> Any:
    """Pass-through process_tool_call that emits control events for Prometheus tool calls."""
    run_id: str = getattr(ctx.deps, "run_id", "")
    t0 = time.monotonic()
    try:
        result = await call_tool(tool_name, tool_args, None)
        duration_ms = int((time.monotonic() - t0) * 1000)
        from app.control.events import emit
        emit("run.tool_call", {"tool": tool_name, "duration_ms": duration_ms, "success": True}, run_id=run_id)
        return result
    except Exception as exc:
        duration_ms = int((time.monotonic() - t0) * 1000)
        from app.control.events import emit
        emit("run.tool_call", {"tool": tool_name, "duration_ms": duration_ms, "success": False, "error": str(exc)}, run_id=run_id)
        raise

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
        process_tool_call=_instrument_process_tool_call,
    )


async def start_mcp() -> MCPServerStreamableHTTP | None:
    """Connect to the Prometheus MCP server. Called during FastAPI lifespan startup."""
    global _mcp_server
    server = _create_mcp_server()
    if server is None:
        return None
    try:
        await server.__aenter__()
        await server.list_tools()  # probe: confirms the server is reachable
        _mcp_server = server
        logger.info("Prometheus MCP connection established (%s)", get_settings().prometheus_mcp_url)
        return server
    except Exception:
        logger.warning("Prometheus MCP not reachable — metrics tools disabled")
        try:
            await server.__aexit__(None, None, None)
        except Exception:
            pass
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
