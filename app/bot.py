"""
Central message dispatch.

Called by channel handlers when a new user message arrives. Responsible for:
  1. Allowlist gate (belt-and-suspenders; the channel handler also checks)
  2. User DB lookup / first-visit auto-create
  3. Running the agent via agent_run() (context assembly, execution, logging)
  4. Persisting the text-only message pair for summarization
  5. Updating the device state cache from any Homey tool calls made during the run
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from time import monotonic

from sqlmodel import Session, select

from app.channels.base import MediaAttachment
from app.config import get_settings
from app.db import users_session
from app.models.users import ChannelMapping, Household, User

logger = logging.getLogger(__name__)

# Per-user sliding-window rate limiter (in-memory; resets on restart).
# Keyed by whatever identifies a "user" for the calling channel — Telegram
# uses int telegram_id, web chat (app/webchat/dispatch.py) reuses this same
# limiter keyed by internal User.id (str), so the two channels never share
# state but don't need two copies of the sliding-window algorithm either.
_user_call_times: dict[int | str, list[float]] = defaultdict(list)

_ONBOARDING_NUDGE = (
    "\n\n_Tip: send /me name Your Name to identify yourself so I can personalise responses._"
)


def _ensure_telegram_channel_mapping(session: "Session", user: "User") -> None:
    """Create a telegram ChannelMapping for the user if one does not exist."""
    from sqlmodel import select as _select

    existing = session.exec(
        _select(ChannelMapping).where(
            ChannelMapping.user_id == user.id,
            ChannelMapping.channel == "telegram",
        )
    ).first()
    if not existing:
        session.add(
            ChannelMapping(
                user_id=user.id,
                channel="telegram",
                channel_user_id=str(user.telegram_id),
            )
        )


def _resolve_existing_telegram_user_id(session: "Session", telegram_id: int) -> str | None:
    """Same lookup as _get_or_create_user, minus the auto-create fallback
    — used by /link to tell "genuinely no identity yet" apart from "this
    telegram_id already belongs to someone" without side effects."""
    mapping = session.exec(
        select(ChannelMapping).where(
            ChannelMapping.channel == "telegram",
            ChannelMapping.channel_user_id == str(telegram_id),
        )
    ).first()
    if mapping:
        return mapping.user_id
    user = session.exec(select(User).where(User.telegram_id == telegram_id)).first()
    return user.id if user else None


async def _handle_link_command(telegram_id: int, text: str) -> str:
    """Special-cased ahead of the normal auto-create-on-first-message path
    (docs/household-identity-and-access-design.md Option B) — a person
    linking a brand-new Telegram account to an existing web-only `User`
    must not first get a throwaway placeholder `User` auto-created for
    this same telegram_id, which `_get_or_create_user` would otherwise do
    before a slash command ever runs."""
    from app.control.audit import record_audit_event
    from app.webchat.link_codes import consume_link_code

    parts = text.split(maxsplit=1)
    code = parts[1].strip() if len(parts) > 1 else ""
    if not code:
        return "Usage: /link <code> — get a code from a household admin."

    with users_session() as session:
        existing_user_id = _resolve_existing_telegram_user_id(session, telegram_id)
        if existing_user_id is not None:
            existing_user = session.exec(select(User).where(User.id == existing_user_id)).first()
            record_audit_event(
                "telegram.link_rejected_already_linked",
                existing_user.household_id if existing_user else "",
                target_user_id=existing_user_id,
                detail={"telegram_id": telegram_id},
            )
            return "This Telegram account is already linked to a household member."

        info = consume_link_code(code)
        if info is None:
            household = session.exec(select(Household)).first()
            record_audit_event(
                "telegram.link_failed",
                household.id if household else "",
                detail={"telegram_id": telegram_id},
            )
            return "That code is invalid or has expired. Ask an admin for a new one."

        user = session.exec(select(User).where(User.id == info.user_id)).first()
        if user is None:
            return "That code is invalid or has expired. Ask an admin for a new one."

        session.add(
            ChannelMapping(user_id=user.id, channel="telegram", channel_user_id=str(telegram_id))
        )
        if user.telegram_id is None:
            user.telegram_id = telegram_id
            session.add(user)
        session.commit()
        user_name = user.name

    record_audit_event(
        "telegram.link_succeeded",
        info.household_id,
        target_user_id=info.user_id,
        detail={"telegram_id": telegram_id},
    )
    logger.info("Telegram account linked (telegram_id=%d, user_id=%s)", telegram_id, info.user_id)
    return f"Linked! This Telegram account is now {user_name}'s."


def _is_rate_limited(key: int | str, limit_per_minute: int) -> bool:
    """Return True if `key` has exceeded limit_per_minute calls in 60 s."""
    now = monotonic()
    calls = _user_call_times[key]
    _user_call_times[key] = [t for t in calls if now - t < 60.0]
    if not _user_call_times[key]:
        del _user_call_times[key]
    if len(_user_call_times.get(key, [])) >= limit_per_minute:
        return True
    _user_call_times[key].append(now)
    return False


@dataclass
class _UserInfo:
    id: str
    name: str
    household_id: str
    household_name: str
    is_admin: bool
    onboarding_complete: bool = False


def _get_or_create_user(telegram_id: int) -> _UserInfo:
    settings = get_settings()
    with users_session() as session:
        # ChannelMapping is the authoritative lookup (docs/household-
        # identity-and-access-design.md Option B) — a User created via a
        # web chat invite has no telegram_id until /link writes this
        # mapping, and linking never touches User.telegram_id-matching
        # logic. The direct telegram_id match below is a defensive
        # fallback for rows that predate this mapping being consulted
        # here; every resolution path (including this one) keeps both in
        # sync via _ensure_telegram_channel_mapping.
        mapping = session.exec(
            select(ChannelMapping).where(
                ChannelMapping.channel == "telegram",
                ChannelMapping.channel_user_id == str(telegram_id),
            )
        ).first()
        user = (
            session.exec(select(User).where(User.id == mapping.user_id)).first()
            if mapping
            else session.exec(select(User).where(User.telegram_id == telegram_id)).first()
        )

        if user:
            household = session.exec(
                select(Household).where(Household.id == user.household_id)
            ).first()
            household_name = household.name if household else "the household"
            _ensure_telegram_channel_mapping(session, user)
            return _UserInfo(
                id=user.id,
                name=user.name,
                household_id=user.household_id,
                household_name=household_name,
                is_admin=user.is_admin,
                onboarding_complete=user.onboarding_complete,
            )

        # First visit — create a household if none exists yet
        household = session.exec(select(Household)).first()
        if not household:
            household = Household(name="My Home")
            session.add(household)
            session.flush()

        new_user = User(
            household_id=household.id,
            telegram_id=telegram_id,
            name="User",  # placeholder — agent will ask for real name
            is_admin=telegram_id in settings.admin_telegram_ids,
        )
        session.add(new_user)
        session.flush()
        _ensure_telegram_channel_mapping(session, new_user)
        session.commit()
        session.refresh(new_user)
        logger.info("New user created (telegram_id=%d)", telegram_id)

        # Immediately link the new user to a HouseholdMember so the world
        # model can mark them as the current speaker without waiting for
        # the next startup bootstrap.
        from app.world.repository import WorldModelRepository

        WorldModelRepository.upsert_member(
            household.id,
            user_id=new_user.id,
            name=new_user.name,
            role="admin" if new_user.is_admin else "member",
            source="migration_seed",
        )

        return _UserInfo(
            id=new_user.id,
            name=new_user.name,
            household_id=household.id,
            household_name=household.name,
            is_admin=new_user.is_admin,
            onboarding_complete=new_user.onboarding_complete,
        )


async def handle_incoming_message(
    telegram_id: int,
    text: str,
    attachments: list[MediaAttachment],
) -> str | None:
    """
    Entry point for all incoming messages (text and/or media).
    Returns the response string to send back, or None to send nothing.
    """
    settings = get_settings()

    if telegram_id not in settings.allowed_telegram_ids:
        return None  # silent drop

    if text.strip().lower().startswith("/link"):
        return await _handle_link_command(telegram_id, text.strip())

    if not (settings.is_development or settings.is_test) and _is_rate_limited(
        telegram_id, settings.rate_limit_per_user_per_minute
    ):
        logger.warning("Rate limit exceeded for telegram_id=%d — dropping message", telegram_id)
        return "You're sending messages too quickly. Please wait a moment before trying again."

    user = _get_or_create_user(telegram_id)

    # Option D's AND, not a hand-off: ALLOWED_TELEGRAM_IDS above is the
    # deploy-time bootstrap ceiling, this is the independently-required,
    # live per-message database check (docs/household-identity-and-
    # access-design.md Phase 2). Both must agree; neither alone suffices.
    from app.policy.authorize import authorize
    from app.policy.principal import load_principal

    decision = authorize(load_principal(user.id), "telegram")
    if not decision.allowed:
        logger.info("Telegram message denied for user_id=%s (%s)", user.id, decision.reason)
        return None  # silent drop — symmetric with the allowlist-miss case above

    if text.startswith("/"):
        from app.commands.dispatcher import try_dispatch

        cmd_response = await try_dispatch(
            text,
            user_id=user.id,
            user_name=user.name,
            telegram_id=telegram_id,
            is_admin=user.is_admin,
            household_id=user.household_id,
        )
        if cmd_response is not None:
            return cmd_response

    from app.agent.runner import agent_run, get_user_run_lock
    from app.channels.registry import get_channel
    from app.homey.state_cache import update_snapshots_from_tool_calls
    from app.memory.conversation import save_message_pair

    channel_user_id = str(telegram_id)

    # Serialize per-user: Q2 waits for Q1 to finish so responses never arrive
    # out of order and context always sees the latest saved history.
    # get_user_run_lock is keyed by user_id and shared with background jobs.
    async with get_user_run_lock(user.id):
        _media_list = attachments or []

        async def _on_retry(attempt: int) -> None:
            if attempt == 0:
                ch = get_channel()
                if ch:
                    try:
                        await ch.send_message(channel_user_id, "One moment — retrying shortly.")
                    except Exception:
                        pass

        outcome = await agent_run(
            text=text,
            user_id=user.id,
            household_id=user.household_id,
            channel_user_id=channel_user_id,
            trigger="user_message",
            user_name=user.name,
            household_name=user.household_name,
            media=_media_list,
            save_history=True,
            retries=2,
            on_retry=_on_retry,
        )

        if not outcome.success:
            return outcome.response

        # Persist text-only message pair for summarization.
        # Build a text label that captures media types when there is no text.
        if not text and _media_list:
            media_label = ", ".join(f"[{a.mime_type.split('/')[0]}]" for a in _media_list)
        else:
            media_label = text
        save_message_pair(user.id, media_label, outcome.response)

        # Update Homey device state cache from tool calls made during this run.
        update_snapshots_from_tool_calls(user.household_id, outcome.new_messages)

        response = outcome.response
        if not user.onboarding_complete and response:
            response = response + _ONBOARDING_NUDGE

        return response
