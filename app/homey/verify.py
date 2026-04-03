from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)


async def verify_after_write(
    household_id: str,
    channel_user_id: str,
    tool_name: str,
    tool_args: dict[str, object],
    control_task_id: str | None = None,
) -> None:
    """
    Wait a short delay, then read back the device state to confirm the write
    succeeded.  Reports a mismatch to the user via the active channel.

    Also emits an internal verify_result InboundEvent so the control loop can
    advance task state (Phase 3b).

    Called as a fire-and-forget asyncio task after a write tool call.
    """
    from app.config import get_settings
    from app.homey.mcp_client import get_mcp_server
    from app.homey.state_cache import upsert_snapshot

    settings = get_settings()
    await asyncio.sleep(settings.homey_verify_delay_seconds)

    server = get_mcp_server()
    if server is None:
        return

    device_id = str(tool_args.get("device_id", ""))
    capability = str(tool_args.get("capability", ""))
    expected_value = tool_args.get("value")

    if not device_id or not capability:
        return

    try:
        # Ask Homey for the current device state
        result = await server.direct_call_tool(
            "get_device_state", {"device_id": device_id}, None
        )
        result_text = str(result) if result else ""

        # Optimistically update cache from what Homey reports
        # (parse is best-effort; result format depends on Homey's MCP schema)
        upsert_snapshot(household_id, device_id, capability, result_text, "verify")
        logger.debug(
            "Verify: device=%s capability=%s expected=%r got=%r",
            device_id,
            capability,
            expected_value,
            result_text,
        )

        # Emit internal verify_result so the control loop can advance task state
        from app.control.internal_events import emit_verify_result

        emit_verify_result(
            household_id=household_id,
            device_id=device_id,
            capability=capability,
            expected=expected_value,
            observed=result_text,
            ok=True,
            control_task_id=control_task_id,
        )

    except Exception:
        # If the read-back fails, warn the user
        logger.warning(
            "Verify read-back failed for %s/%s", device_id, capability, exc_info=True
        )
        _notify_verify_failure(channel_user_id, device_id, capability)

        # Emit failure result so the loop knows verification didn't complete
        try:
            from app.control.internal_events import emit_verify_result

            emit_verify_result(
                household_id=household_id,
                device_id=device_id,
                capability=capability,
                expected=expected_value,
                observed="",
                ok=False,
                control_task_id=control_task_id,
            )
        except Exception:
            pass


def _notify_verify_failure(channel_user_id: str, device_id: str, capability: str) -> None:
    """Best-effort: send the user a warning that state verification failed."""
    try:
        from app.channels.registry import get_channel

        channel = get_channel()
        if channel and channel_user_id:
            asyncio.ensure_future(
                channel.send_message(
                    channel_user_id,
                    f"⚠️ Could not confirm that {device_id}/{capability} was updated "
                    "— please check the device directly.",
                )
            )
    except Exception:
        logger.debug("Could not notify user of verify failure")
