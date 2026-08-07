"""MCP client wiring for Oda's remote grocery MCP server (https://oda.com/mcp).

Mirrors app/homey/mcp_client.py's shape (module singleton, start_mcp()/
stop_mcp() with retry/backoff, policy-gated process_tool_call), with two
differences:

- Auth is per-household (an IntegrationAccount via OAuth), not a bare URL,
  so start_mcp() resolves "the" household the same simple way app.bot and
  the admin routes do — this app assumes a single household per deployment,
  same as Homey/Prometheus already do.
- No verify-after-write step. Homey's policy gate schedules a state
  verification poll after write tool calls because physical device state
  can lag or fail silently; there's no equivalent concept for a grocery
  cart — manipulate_cart's response already reflects the new cart state
  synchronously.

See docs/oda-grocery-mcp-tool-design.md "MCP client wiring".
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, AsyncGenerator

import httpx
from pydantic_ai import RunContext
from pydantic_ai.mcp import CallToolFunc, MCPToolset, ToolResult

from app.oda.oauth import MCP_URL

logger = logging.getLogger(__name__)

_mcp_server: MCPToolset | None = None

# Oda tool responses (cart, orders, product search) are much smaller than
# Homey's home-structure dumps — 20,000 chars (~5,000 tokens) is generous
# headroom while still protecting the per-minute token budget.
_MAX_TOOL_RESULT_CHARS = 20_000


def get_mcp_server() -> MCPToolset | None:
    """Return the running Oda MCP server instance, or None if not connected."""
    return _mcp_server


class OdaTokenAuth(httpx.Auth):
    """Attaches a Bearer token from the household's IntegrationAccount to
    every request, refreshing (single-flight — see
    app.integrations.accounts.get_valid_access_token) on a 401."""

    def __init__(self, household_id: str) -> None:
        self._household_id = household_id

    async def async_auth_flow(
        self, request: httpx.Request
    ) -> AsyncGenerator[httpx.Request, httpx.Response]:
        from app.integrations.accounts import get_valid_access_token

        token = await get_valid_access_token(self._household_id, "oda")
        if token:
            request.headers["Authorization"] = f"Bearer {token}"
        response = yield request

        if response.status_code == 401:
            token = await get_valid_access_token(self._household_id, "oda")
            if token:
                request.headers["Authorization"] = f"Bearer {token}"
                yield request


async def _policy_process_tool_call(
    ctx: RunContext[Any],
    call_tool: CallToolFunc,
    tool_name: str,
    tool_args: dict[str, Any],
) -> ToolResult:
    """Policy gate callback for every Oda MCP tool call — same shape as
    Homey's, minus the verify-after-write scheduling (see module docstring)."""
    from app.policy.gate import evaluate_policy

    decision = evaluate_policy(tool_name, tool_args)

    if not decision.requires_confirm:
        from app.config import get_settings as _get_settings

        run_id: str = getattr(ctx.deps, "run_id", "")
        t0 = time.monotonic()
        try:
            result = await asyncio.wait_for(
                call_tool(tool_name, tool_args),
                timeout=_get_settings().oda_tool_timeout_secs,
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
                    "tool": tool_name,
                    "duration_ms": duration_ms,
                    "success": False,
                    "error": "timeout",
                },
                run_id=run_id,
            )
            timeout = _get_settings().oda_tool_timeout_secs
            return f"Oda did not respond within {timeout} s — please try again."
        except Exception as exc:
            duration_ms = int((time.monotonic() - t0) * 1000)
            from app.control.events import emit

            emit(
                "run.tool_call",
                {
                    "tool": tool_name,
                    "duration_ms": duration_ms,
                    "success": False,
                    "error": str(exc),
                },
                run_id=run_id,
            )
            raise

        if isinstance(result, str) and len(result) > _MAX_TOOL_RESULT_CHARS:
            result = result[:_MAX_TOOL_RESULT_CHARS] + "\n[...truncated]"

        return result

    # Confirmation required — save PendingAction and send inline prompt
    from app.channels.registry import get_channel
    from app.policy.pending import save_pending_action

    household_id = str(getattr(ctx.deps, "household_id", ""))
    user_id = str(getattr(ctx.deps, "user_id", ""))
    channel_user_id = str(getattr(ctx.deps, "channel_user_id", ""))
    channel_name = str(getattr(ctx.deps, "channel", "telegram"))

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

    channel = get_channel(channel_name)
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


def _resolve_household_id() -> str | None:
    """Same "single household per deployment" resolution app.bot and the
    admin routes already use — see docs/oda-grocery-mcp-tool-design.md
    "Household account model"."""
    from sqlmodel import select

    from app.db import users_session
    from app.models.users import Household

    with users_session() as session:
        household = session.exec(select(Household)).first()
    return household.id if household else None


def _create_mcp_server(household_id: str) -> MCPToolset:
    return MCPToolset(
        MCP_URL,
        auth=OdaTokenAuth(household_id),
        process_tool_call=_policy_process_tool_call,
    )


_MCP_CONNECT_TIMEOUT = 10  # seconds per attempt
_MCP_MAX_RETRIES = 3
_MCP_RETRY_BACKOFF = 5  # seconds between retries


async def start_mcp() -> MCPToolset | None:
    """
    Connect to Oda's MCP server and register it as the module singleton.

    Called during FastAPI lifespan startup (and again after a successful
    admin connect — see app.api.integrations). No-ops cleanly, same as
    Homey/Prometheus with no URL configured, when there's no household or
    the household hasn't connected an Oda account yet.
    """
    global _mcp_server

    household_id = _resolve_household_id()
    if not household_id:
        logger.info("Oda MCP: no household found — grocery tools disabled")
        return None

    from app.integrations.accounts import get_account

    if get_account(household_id, "oda") is None:
        logger.info("Oda MCP: household not connected to Oda — grocery tools disabled")
        return None

    server = _create_mcp_server(household_id)
    for attempt in range(1, _MCP_MAX_RETRIES + 1):
        try:
            await server.__aenter__()
            await asyncio.wait_for(
                server.list_tools(),
                timeout=_MCP_CONNECT_TIMEOUT,
            )
            _mcp_server = server
            logger.info("Oda MCP connection established (household=%s)", household_id)
            return server
        except (asyncio.TimeoutError, Exception) as exc:
            try:
                await server.__aexit__(None, None, None)
            except Exception:
                pass
            if attempt < _MCP_MAX_RETRIES:
                logger.warning(
                    "Oda MCP not reachable (attempt %d/%d: %s) — retrying in %ds",
                    attempt,
                    _MCP_MAX_RETRIES,
                    exc,
                    _MCP_RETRY_BACKOFF,
                )
                await asyncio.sleep(_MCP_RETRY_BACKOFF)
                server = _create_mcp_server(household_id)
            else:
                logger.warning(
                    "Oda MCP not reachable after %d attempts — grocery tools disabled",
                    _MCP_MAX_RETRIES,
                )
    return None


async def stop_mcp() -> None:
    """
    Disconnect from Oda's MCP server. Called during FastAPI lifespan
    shutdown, and after a successful admin disconnect.
    """
    global _mcp_server
    if _mcp_server is not None:
        try:
            await _mcp_server.__aexit__(None, None, None)
        except Exception:
            logger.warning("Error during Oda MCP shutdown", exc_info=True)
        finally:
            _mcp_server = None
            logger.info("Oda MCP connection closed")
