from __future__ import annotations

import logging
from time import monotonic

logger = logging.getLogger(__name__)


async def try_dispatch(
    text: str,
    user_id: str,
    user_name: str,
    telegram_id: int,
    is_admin: bool,
    household_id: str,
) -> str | None:
    """
    If *text* starts with '/', attempt to dispatch it as a slash command.

    Returns the response string if the command was handled, or None if the
    text is not a slash command (so the caller falls through to the LLM).
    """
    if not text.startswith("/"):
        return None

    tokens = text.lstrip("/").split()
    if not tokens:
        return None

    name = tokens[0].lower()
    args = tokens[1:]

    from app.commands.handlers import registry

    cmd = registry.get(name)
    if cmd is None:
        return f"Unknown command /{name}. Type /help for a list of available commands."

    if cmd.admin_only and not is_admin:
        logger.info("Non-admin user %s attempted admin command /%s", user_id[:8], name)
        return f"/{name} is restricted to administrators."

    from app.commands.registry import SlashCommandContext
    from app.control.events import emit

    ctx = SlashCommandContext(
        raw_text=text,
        args=args,
        user_id=user_id,
        user_name=user_name,
        telegram_id=telegram_id,
        is_admin=is_admin,
        household_id=household_id,
    )

    t_start = monotonic()
    success = True
    try:
        response = await cmd.run(ctx)
    except Exception:
        logger.exception("Slash command /%s raised an exception", name)
        success = False
        response = f"/{name} failed — please try again."

    duration_ms = int((monotonic() - t_start) * 1000)
    emit(
        "cmd.dispatch",
        {"command": name, "user_id": user_id, "duration_ms": duration_ms, "success": success},
    )
    logger.info("Slash command /%s handled in %d ms (success=%s)", name, duration_ms, success)

    return response
