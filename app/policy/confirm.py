"""
Shared PendingAction confirm/cancel execution.

Both Telegram (inline buttons) and the web chat channel (WS confirm/cancel
messages) drive the same policy-gate confirmation flow. This module holds
the channel-agnostic core — ownership check, MCP execution, verify
scheduling, conversation bookkeeping — so channel adapters only render the
result in their own UI idiom instead of duplicating this logic.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class ConfirmResult:
    ok: bool
    message: str  # human-readable outcome, safe to show directly to the user
    status: str  # "executed" | "cancelled" | "failed" | "expired" | "not_owner"


async def execute_pending_action(
    token: str,
    requesting_user_id: str,
    channel_user_id: str,
    channel: str = "telegram",
) -> ConfirmResult:
    """Look up, execute, and clean up a PendingAction.

    requesting_user_id: internal User.id resolved by the caller from the
        confirming channel identity (Telegram user, web session, ...).
    channel_user_id / channel: where a verify-after-write failure follow-up
        should be delivered if the write doesn't verify.
    """
    from app.policy.pending import delete_pending_action, get_pending_action

    action = get_pending_action(token)
    if action is None:
        return ConfirmResult(
            ok=False, message="This action has expired or was already handled.", status="expired"
        )

    if not requesting_user_id or requesting_user_id != action.user_id:
        return ConfirmResult(
            ok=False, message="This action doesn't belong to you.", status="not_owner"
        )

    # Delete first — a second confirm on the same token immediately reads as
    # "expired", preventing double-execution while this one is still in flight.
    delete_pending_action(token)

    from app.homey.mcp_client import get_mcp_server

    server = get_mcp_server()
    if server is None:
        return ConfirmResult(
            ok=False, message="Homey is not connected — cannot execute.", status="failed"
        )

    from app.memory.conversation import save_message_pair

    try:
        tool_args: dict[str, object] = json.loads(action.tool_args)
        result = await server.direct_call_tool(action.tool_name, tool_args)

        logger.info("Confirmed action executed: %s (token=%s)", action.tool_name, token)

        # Persist to conversation history so the agent doesn't re-prompt next message
        save_message_pair(
            action.user_id,
            "[User confirmed action]",
            f"The action '{action.tool_name}' was confirmed by the user"
            " and executed successfully. No further confirmation is needed.",
        )

        from app.homey.verify import verify_after_write

        asyncio.ensure_future(
            verify_after_write(
                action.household_id,
                channel_user_id,
                action.tool_name,
                tool_args,
                channel=channel,
            )
        )

        return ConfirmResult(ok=True, message=f"Done: {result}", status="executed")
    except Exception:
        logger.exception("Failed to execute confirmed action (token=%s)", token)
        # Persist failure so the agent doesn't keep re-prompting for the same action
        save_message_pair(
            action.user_id,
            "[User confirmed action — action failed]",
            f"The action '{action.tool_name}' was confirmed by the user but failed to execute."
            " The user has been notified. Do not retry this action automatically.",
        )
        return ConfirmResult(
            ok=False,
            message="Action failed — please check the device and try again.",
            status="failed",
        )


async def cancel_pending_action_for_user(token: str, requesting_user_id: str) -> ConfirmResult:
    """Cancel a PendingAction on behalf of requesting_user_id, if they own it."""
    from app.policy.pending import delete_pending_action, get_pending_action

    action = get_pending_action(token)
    if action is None:
        return ConfirmResult(
            ok=False, message="This action has expired or was already handled.", status="expired"
        )

    if not requesting_user_id or requesting_user_id != action.user_id:
        return ConfirmResult(
            ok=False, message="This action doesn't belong to you.", status="not_owner"
        )

    delete_pending_action(token)
    logger.info("Pending action cancelled (token=%s)", token)
    return ConfirmResult(ok=True, message="Action cancelled.", status="cancelled")
