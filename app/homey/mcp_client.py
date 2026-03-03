from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from pydantic_ai import RunContext
from pydantic_ai.mcp import MCPServerStreamableHTTP

from app.config import get_settings

logger = logging.getLogger(__name__)

_mcp_server: MCPServerStreamableHTTP | None = None

# Type alias for the inner call_tool callable passed to process_tool_call
_DirectCallFn = Callable[[str, dict[str, Any], Any], Awaitable[Any]]


def get_mcp_server() -> MCPServerStreamableHTTP | None:
    """Return the running Homey MCP server instance, or None if not configured."""
    return _mcp_server


async def _policy_process_tool_call(
    ctx: RunContext[Any],
    call_tool: _DirectCallFn,
    tool_name: str,
    tool_args: dict[str, Any],
) -> Any:
    """
    Policy gate callback for every Homey MCP tool call.

    - Evaluates the policy for the tool/args combination.
    - If confirmation is required: saves a PendingAction, sends the user an
      inline Yes/No prompt, and returns a holding message string.
    - If no confirmation needed: executes the tool and schedules a state
      verification task for write operations.
    """
    from app.policy.gate import evaluate_policy

    decision = evaluate_policy(tool_name, tool_args)

    if not decision.requires_confirm:
        result = await call_tool(tool_name, tool_args, None)

        # Schedule a non-blocking state verification for write tool calls
        if _is_write_tool(tool_name):
            channel_user_id: str = getattr(ctx.deps, "channel_user_id", "")
            household_id: str = getattr(ctx.deps, "household_id", "")
            if channel_user_id and household_id:
                from app.homey.verify import verify_after_write

                asyncio.ensure_future(
                    verify_after_write(household_id, channel_user_id, tool_name, tool_args)
                )

        return result

    # Confirmation required — save PendingAction and send inline prompt
    from app.channels.registry import get_channel
    from app.policy.pending import save_pending_action

    household_id = str(getattr(ctx.deps, "household_id", ""))
    user_id = str(getattr(ctx.deps, "user_id", ""))
    channel_user_id = str(getattr(ctx.deps, "channel_user_id", ""))

    if not household_id or not user_id:
        logger.warning(
            "Policy gate triggered but deps missing household_id/user_id — allowing tool"
        )
        return await call_tool(tool_name, tool_args, None)

    token = save_pending_action(
        household_id=household_id,
        user_id=user_id,
        tool_name=tool_name,
        tool_args=tool_args,
        policy_name=decision.policy_name,
    )

    channel = get_channel()
    if channel and channel_user_id:
        await channel.send_confirmation_prompt(
            channel_user_id,
            decision.confirm_message,
            token,
        )
        logger.info(
            "Policy gate: confirmation required for '%s' (policy=%s, token=%s)",
            tool_name,
            decision.policy_name,
            token,
        )
    else:
        logger.warning("Policy gate: no channel available to send confirmation prompt")

    return (
        f"Action '{decision.policy_name}' requires your confirmation. "
        "I've sent you an inline confirmation button — please approve or cancel."
    )


def _is_write_tool(tool_name: str) -> bool:
    """Heuristic: is this a state-changing tool call?"""
    write_prefixes = ("set_", "trigger_", "lock_", "unlock_", "turn_")
    return any(tool_name.startswith(p) for p in write_prefixes)


def _create_mcp_server() -> MCPServerStreamableHTTP | None:
    """Instantiate an MCPServerStreamableHTTP from current settings, or return None."""
    settings = get_settings()
    if not settings.homey_mcp_url:
        logger.info("Homey MCP not configured — smart home tools disabled")
        return None

    return MCPServerStreamableHTTP(
        url=settings.homey_mcp_url,
        tool_prefix="homey",
        process_tool_call=_policy_process_tool_call,
    )


async def start_mcp() -> MCPServerStreamableHTTP | None:
    """
    Connect to the Homey MCP server and register it as the module singleton.

    Called during FastAPI lifespan startup.  Returns the server instance on
    success, or None if Homey is not configured.
    """
    global _mcp_server
    server = _create_mcp_server()
    if server is None:
        return None
    try:
        await server.__aenter__()
        _mcp_server = server
        logger.info("Homey MCP connection established (%s)", get_settings().homey_mcp_url)
        return server
    except Exception:
        logger.exception("Failed to connect to Homey MCP — smart home tools disabled")
        return None


async def stop_mcp() -> None:
    """
    Disconnect from the Homey MCP server.

    Called during FastAPI lifespan shutdown.
    """
    global _mcp_server
    if _mcp_server is not None:
        try:
            await _mcp_server.__aexit__(None, None, None)
        except Exception:
            logger.warning("Error during Homey MCP shutdown", exc_info=True)
        finally:
            _mcp_server = None
            logger.info("Homey MCP connection closed")
