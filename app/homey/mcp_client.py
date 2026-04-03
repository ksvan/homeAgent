from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from pydantic_ai import RunContext
from pydantic_ai.mcp import MCPServerStreamableHTTP

from app.config import get_settings

logger = logging.getLogger(__name__)

_mcp_server: MCPServerStreamableHTTP | None = None

# Cap MCP tool responses to prevent token budget blowout.
# homey_get_home_structure can return 20k+ chars for large homes; raised to
# 40,000 chars (~10,000 tokens) to allow full home structure responses.
_MAX_TOOL_RESULT_CHARS = 40_000

# Tools included in the "simple" schema — day-to-day home control.
# Homey AI Chat Control uses a meta-tool pattern: search_tools + use_tool for
# all device actions, plus three read-only structural tools.
_SIMPLE_TOOLS: frozenset[str] = frozenset(
    {
        "homey_search_tools",
        "homey_use_tool",
        "homey_get_home_structure",
        "homey_get_states",
        "homey_get_flow_overview",
    }
)

# Type alias for the inner call_tool callable passed to process_tool_call
_DirectCallFn = Callable[[str, dict[str, Any], Any], Awaitable[Any]]


def get_mcp_server() -> MCPServerStreamableHTTP | None:
    """Return the running Homey MCP server instance, or None if not configured."""
    return _mcp_server


def get_mcp_toolset(advanced: bool = False):
    """Return a Homey toolset for the agent.

    By default returns the simple schema (7 tools for everyday actions).
    Pass advanced=True to expose all tools including flow creation and device management.
    """
    if _mcp_server is None:
        return None
    if advanced:
        return _mcp_server
    return _mcp_server.filtered(lambda _ctx, tool: tool.name in _SIMPLE_TOOLS)


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
        from app.config import get_settings as _get_settings
        run_id: str = getattr(ctx.deps, "run_id", "")
        t0 = time.monotonic()
        try:
            result = await asyncio.wait_for(
                call_tool(tool_name, tool_args, None),
                timeout=_get_settings().homey_tool_timeout_secs,
            )
            duration_ms = int((time.monotonic() - t0) * 1000)
            from app.control.events import emit
            emit(
                "run.tool_call",
                {"tool": tool_name, "duration_ms": duration_ms, "success": True},
                run_id=run_id,
            )
        except asyncio.TimeoutError:
            duration_ms = int((time.monotonic() - t0) * 1000)
            from app.control.events import emit
            emit(
                "run.tool_call",
                {
                    "tool": tool_name, "duration_ms": duration_ms,
                    "success": False, "error": "timeout",
                },
                run_id=run_id,
            )
            timeout = _get_settings().homey_tool_timeout_secs
            return f"Homey did not respond within {timeout} s — please try again."
        except Exception as exc:
            duration_ms = int((time.monotonic() - t0) * 1000)
            from app.control.events import emit
            emit(
                "run.tool_call",
                {
                    "tool": tool_name, "duration_ms": duration_ms,
                    "success": False, "error": str(exc),
                },
                run_id=run_id,
            )
            raise

        # Schedule a non-blocking state verification for write tool calls
        if _is_write_tool(tool_name):
            channel_user_id: str = getattr(ctx.deps, "channel_user_id", "")
            household_id: str = getattr(ctx.deps, "household_id", "")
            if channel_user_id and household_id:
                from app.homey.verify import verify_after_write

                _control_task_id: str | None = getattr(ctx.deps, "control_task_id", None) or None
                asyncio.ensure_future(
                    verify_after_write(
                        household_id,
                        channel_user_id,
                        tool_name,
                        tool_args,
                        control_task_id=_control_task_id,
                    )
                )

        # Truncate large tool results to stay within per-minute token rate limits
        if isinstance(result, str) and len(result) > _MAX_TOOL_RESULT_CHARS:
            result = result[:_MAX_TOOL_RESULT_CHARS] + "\n[...truncated]"

        return result

    # Confirmation required — save PendingAction and send inline prompt
    from app.channels.registry import get_channel
    from app.policy.pending import save_pending_action

    household_id = str(getattr(ctx.deps, "household_id", ""))
    user_id = str(getattr(ctx.deps, "user_id", ""))
    channel_user_id = str(getattr(ctx.deps, "channel_user_id", ""))

    if not household_id or not user_id:
        logger.warning(
            "Policy gate: deps missing household_id/user_id for tool=%s — denying", tool_name
        )
        return (
            "Action blocked — session context is incomplete. "
            "Please restart the conversation and try again."
        )

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


_MCP_CONNECT_TIMEOUT = 10  # seconds per attempt
_MCP_MAX_RETRIES = 3
_MCP_RETRY_BACKOFF = 5  # seconds between retries


async def start_mcp() -> MCPServerStreamableHTTP | None:
    """
    Connect to the Homey MCP server and register it as the module singleton.

    Called during FastAPI lifespan startup.  Retries up to 3 times with
    backoff.  Returns the server instance on success, or None if Homey is
    not configured or unreachable.
    """
    global _mcp_server
    server = _create_mcp_server()
    if server is None:
        return None

    for attempt in range(1, _MCP_MAX_RETRIES + 1):
        try:
            await server.__aenter__()
            await asyncio.wait_for(
                server.list_tools(),
                timeout=_MCP_CONNECT_TIMEOUT,
            )
            _mcp_server = server
            logger.info("Homey MCP connection established (%s)", get_settings().homey_mcp_url)
            return server
        except (asyncio.TimeoutError, Exception) as exc:
            try:
                await server.__aexit__(None, None, None)
            except Exception:
                pass
            if attempt < _MCP_MAX_RETRIES:
                logger.warning(
                    "Homey MCP not reachable (attempt %d/%d: %s) — retrying in %ds",
                    attempt, _MCP_MAX_RETRIES, exc, _MCP_RETRY_BACKOFF,
                )
                await asyncio.sleep(_MCP_RETRY_BACKOFF)
                server = _create_mcp_server()
                if server is None:
                    return None
            else:
                logger.warning(
                    "Homey MCP not reachable after %d attempts — smart home tools disabled",
                    _MCP_MAX_RETRIES,
                )
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
