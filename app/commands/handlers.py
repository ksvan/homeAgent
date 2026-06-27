from __future__ import annotations

import json
import logging

from app.commands.registry import SlashCommand, SlashCommandContext, SlashCommandRegistry

logger = logging.getLogger(__name__)

registry = SlashCommandRegistry()


class _Help(SlashCommand):
    name = "help"
    help = "Show available commands"

    async def run(self, ctx: SlashCommandContext) -> str:
        cmds = registry.list_visible(ctx.is_admin)
        lines = []
        for cmd in cmds:
            tag = "  [admin]" if cmd.admin_only else ""
            lines.append(f"/{cmd.name:<16} {cmd.help}{tag}")
        return "Available commands:\n\n" + "\n".join(lines)


class _ContextStats(SlashCommand):
    name = "contextstats"
    help = "Show context size breakdown for the next LLM call"

    async def run(self, ctx: SlashCommandContext) -> str:
        from datetime import datetime
        from datetime import timezone as _utc

        from app.agent.context import assemble_context
        from app.agent.prompts import load_instructions, load_persona
        from app.config import get_settings

        settings = get_settings()
        assembled = assemble_context(ctx.user_id, ctx.household_id, ctx.raw_text)

        # Prompt files (base system prompt)
        prompt_vars: dict[str, str] = {
            "agent_name": settings.agent_name,
            "household_name": "",
            "current_date": "",
            "current_time": "",
            "timezone": settings.household_timezone,
        }
        persona_chars = len(load_persona(prompt_vars))
        instructions_chars = len(load_instructions(prompt_vars))

        # Dynamic context sections
        msg_chars = sum(
            len(str(getattr(part, "content", "")))
            for msg in assembled.recent_messages
            for part in msg.parts
        )
        summary_chars = len(assembled.conversation_summary or "")
        user_profile_chars = len(assembled.user_profile_text)
        household_profile_chars = len(assembled.household_profile_text)
        world_model_chars = len(assembled.world_model_text)
        mem_count = len(assembled.relevant_memories)
        mem_chars = sum(len(m) for m in assembled.relevant_memories)
        total_chars = (
            persona_chars
            + instructions_chars
            + msg_chars
            + summary_chars
            + user_profile_chars
            + household_profile_chars
            + world_model_chars
            + mem_chars
        )
        approx_tokens = total_chars // 4

        try:
            import datetime as _dt
            from zoneinfo import ZoneInfo

            tz: _dt.tzinfo = ZoneInfo(settings.household_timezone)
        except Exception:
            tz = _utc.utc
        now = datetime.now(tz)
        offset = now.strftime("%z")
        utc_offset = f"{offset[:3]}:{offset[3:]}"
        date_str = now.strftime("%A, %d %B %Y")
        time_str = now.strftime("%H:%M") + f" (UTC{utc_offset})"

        return (
            f"Context breakdown:\n\n"
            f"  Date/time in ctx : {date_str}, {time_str}\n\n"
            f"  System prompt:\n"
            f"    Persona        : {persona_chars:,} chars\n"
            f"    Instructions   : {instructions_chars:,} chars\n\n"
            f"  Dynamic context:\n"
            f"    Recent messages: {len(assembled.recent_messages)} ({msg_chars:,} chars)\n"
            f"    Summary        : {summary_chars:,} chars\n"
            f"    User profile   : {user_profile_chars:,} chars\n"
            f"    Household prof.: {household_profile_chars:,} chars\n"
            f"    World model    : {world_model_chars:,} chars\n"
            f"    Memories       : {mem_count} ({mem_chars:,} chars)\n"
            f"  ──────────────────────────────────────\n"
            f"  Total            : {total_chars:,} chars (~{approx_tokens:,} tokens)"
        )


class _History(SlashCommand):
    name = "history"
    help = "Show recent conversation (usage: /history [n], default 10)"

    async def run(self, ctx: SlashCommandContext) -> str:
        from pydantic_ai.messages import ModelRequest

        from app.memory.conversation import get_conversation_summary, load_recent_messages

        n = 10
        if ctx.args:
            try:
                n = max(1, min(int(ctx.args[0]), 40))
            except ValueError:
                return "Usage: /history [n]  (n must be a number)"

        messages = load_recent_messages(ctx.user_id)
        messages = messages[-n:]

        if not messages:
            return "No conversation history found."

        lines = []
        for msg in messages:
            role = "You" if isinstance(msg, ModelRequest) else "Assistant"
            for part in msg.parts:
                content = str(getattr(part, "content", ""))
                if content:
                    if len(content) > 400:
                        content = content[:400] + "…"
                    lines.append(f"{role}: {content}")

        summary = get_conversation_summary(ctx.user_id)
        header = f"Last {len(messages)} message(s):\n\n"
        body = "\n\n".join(lines)
        footer = "\n\n[Older messages are covered by a conversation summary.]" if summary else ""
        return header + body + footer


class _Schedule(SlashCommand):
    name = "schedule"
    help = "List active reminders and scheduled Homey actions"

    async def run(self, ctx: SlashCommandContext) -> str:
        from sqlmodel import col, select

        from app.db import users_session
        from app.models.tasks import Task

        with users_session() as session:
            tasks = session.exec(
                select(Task).where(
                    Task.user_id == ctx.user_id,
                    col(Task.status) == "ACTIVE",
                )
            ).all()

        if not tasks:
            return "Nothing scheduled."

        lines = []
        for task in tasks:
            task_data: dict[str, object] = {}
            try:
                task_data = json.loads(task.context)
            except Exception:
                pass

            scheduled_at = task_data.get("scheduled_at", "?")
            if "action_tool" in task_data:
                kind = "Action"
                desc = task_data.get("action_description", task.title)
            else:
                kind = "Reminder"
                desc = task_data.get("reminder_text", task.title)

            desc = str(desc)
            if len(desc) > 60:
                desc = desc[:60] + "…"
            lines.append(f"[{kind}] {scheduled_at}  {desc}  (id: {task.id[:8]})")

        return f"{len(tasks)} scheduled item(s):\n\n" + "\n".join(lines)


class _ScheduledPrompts(SlashCommand):
    name = "prompts"
    help = "List recurring scheduled prompts (or: /prompts run <id>)"

    async def run(self, ctx: SlashCommandContext) -> str:
        from sqlmodel import select

        from app.db import users_session
        from app.models.scheduled_prompts import ScheduledPrompt

        # /prompts run <id-prefix>
        if ctx.args and ctx.args[0] == "run":
            if len(ctx.args) < 2:
                return "Usage: /prompts run <id>"
            id_prefix = ctx.args[1].lower()

            with users_session() as session:
                prompts = session.exec(
                    select(ScheduledPrompt).where(ScheduledPrompt.household_id == ctx.household_id)
                ).all()
                match = next((p for p in prompts if p.id.lower().startswith(id_prefix)), None)

            if match is None:
                return f"No prompt found with id starting '{id_prefix}'."

            from app.scheduler.jobs import fire_scheduled_prompt

            try:
                await fire_scheduled_prompt(
                    prompt_id=match.id,
                    user_id=match.user_id,
                    household_id=match.household_id,
                    channel_user_id=match.channel_user_id,
                    prompt_text=match.prompt,
                    name=match.name,
                    is_one_shot=match.recurrence == "once",
                )
            except Exception as exc:
                return f"Prompt '{match.name}' failed: {exc}"
            return f"Fired '{match.name}' — response delivered to its channel."

        # Default: list all prompts
        with users_session() as session:
            prompts = session.exec(
                select(ScheduledPrompt).where(ScheduledPrompt.household_id == ctx.household_id)
            ).all()

        if not prompts:
            return "No scheduled prompts."

        from app.scheduler.scheduled_prompts import recurrence_label

        lines = []
        for p in prompts:
            status = "on" if p.enabled else "off"
            text = p.prompt if len(p.prompt) <= 60 else p.prompt[:60] + "…"
            label = recurrence_label(p.recurrence, p.time_of_day, p.run_at)
            lines.append(f"[{status}] {p.name}  —  {label}\n       {text}  (id: {p.id[:8]})")

        return f"{len(prompts)} scheduled prompt(s):\n\n" + "\n\n".join(lines)


class _Status(SlashCommand):
    name = "status"
    help = "Operational status snapshot (or: /status refresh to reconnect)"
    admin_only = True

    async def run(self, ctx: SlashCommandContext) -> str:
        if ctx.args and ctx.args[0] == "refresh":
            return await self._refresh()
        return self._snapshot()

    def _snapshot(self) -> str:
        from app.homey.mcp_client import get_mcp_server as get_homey
        from app.prometheus.mcp_client import get_mcp_server as get_prom
        from app.scheduler.engine import get_scheduler
        from app.tools.mcp_client import get_mcp_server as get_tools

        def _mark(ok: bool) -> str:
            return "ok" if ok else "unavailable"

        lines = [
            f"Scheduler       : {_mark(get_scheduler() is not None)}",
            f"Homey MCP       : {_mark(get_homey() is not None)}",
            f"Prometheus MCP  : {_mark(get_prom() is not None)}",
            f"Tools MCP       : {_mark(get_tools() is not None)}",
        ]
        return "Status:\n\n" + "\n".join(lines)

    async def _refresh(self) -> str:
        from app.agent.agent import reload_agent
        from app.homey.mcp_client import get_mcp_server as get_homey
        from app.homey.mcp_client import start_mcp as start_homey
        from app.homey.mcp_client import stop_mcp as stop_homey
        from app.prometheus.mcp_client import get_mcp_server as get_prom
        from app.prometheus.mcp_client import start_mcp as start_prom
        from app.prometheus.mcp_client import stop_mcp as stop_prom
        from app.tools.mcp_client import get_mcp_server as get_tools
        from app.tools.mcp_client import start_mcp as start_tools
        from app.tools.mcp_client import stop_mcp as stop_tools

        services = [
            ("Homey MCP", get_homey, stop_homey, start_homey),
            ("Prometheus MCP", get_prom, stop_prom, start_prom),
            ("Tools MCP", get_tools, stop_tools, start_tools),
        ]

        lines = []
        agent_needs_reload = False

        for name, getter, stopper, starter in services:
            if getter() is not None:
                lines.append(f"{name}: ok")
                continue
            try:
                await stopper()
            except Exception:
                pass
            result = await starter()
            if result is not None:
                lines.append(f"{name}: reconnected")
                agent_needs_reload = True
            else:
                lines.append(f"{name}: still unavailable")

        if agent_needs_reload:
            reload_agent()
            lines.append("\nAgent reloaded — reconnected tools are now active.")
        else:
            lines.append("\nNo services recovered — agent unchanged.")

        return "Status refresh:\n\n" + "\n".join(lines)


class _Users(SlashCommand):
    name = "users"
    help = "List household members"
    admin_only = True

    async def run(self, ctx: SlashCommandContext) -> str:
        from sqlmodel import select

        from app.db import users_session
        from app.models.users import User

        with users_session() as session:
            users = session.exec(select(User).where(User.household_id == ctx.household_id)).all()

        if not users:
            return "No users found."

        lines = []
        for u in users:
            admin_tag = "  [admin]" if u.is_admin else ""
            lines.append(f"{u.name}  (tg: {u.telegram_id}){admin_tag}")

        return f"{len(users)} user(s):\n\n" + "\n".join(lines)


class _Me(SlashCommand):
    name = "me"
    help = "View or update your identity (/me show | /me name <name> | /me email <address>)"

    async def run(self, ctx: SlashCommandContext) -> str:

        sub = ctx.args[0].lower() if ctx.args else ""

        if sub == "show":
            return await self._show(ctx)
        if sub == "name":
            name = " ".join(ctx.args[1:]).strip()
            if not name:
                return "Usage: /me name Your Name"
            return await self._set_name(ctx, name)
        if sub == "email":
            if len(ctx.args) >= 3 and ctx.args[1].lower() == "remove":
                return await self._remove_email(ctx, ctx.args[2].strip())
            address = ctx.args[1].strip() if len(ctx.args) >= 2 else ""
            if not address:
                return "Usage: /me email address@example.com"
            return await self._set_email(ctx, address)

        return (
            "Identity commands:\n"
            "/me show — show your linked identity\n"
            "/me name Your Name — set your display name\n"
            "/me email address@example.com — link an email address\n"
            "/me email remove address@example.com — remove an email mapping"
        )

    async def _show(self, ctx: SlashCommandContext) -> str:
        from sqlmodel import select

        from app.db import users_session
        from app.models.users import ChannelMapping, User
        from app.world.repository import WorldModelRepository

        with users_session() as session:
            user = session.exec(select(User).where(User.id == ctx.user_id)).first()
            emails = session.exec(
                select(ChannelMapping).where(
                    ChannelMapping.user_id == ctx.user_id,
                    ChannelMapping.channel == "email",
                )
            ).all()

        if not user:
            return "User not found."

        member = WorldModelRepository.get_member_for_user(ctx.household_id, ctx.user_id)
        member_name = member.name if member else "(not linked)"
        email_lines = "\n".join(f"Email: {e.channel_user_id}" for e in emails) or "Email: (none)"
        role_tag = "admin" if user.is_admin else "member"
        onboarding = "yes" if user.onboarding_complete else "no"

        return (
            f"You are {user.name}.\n"
            f"Telegram id: {user.telegram_id}\n"
            f"{email_lines}\n"
            f"Role: {role_tag}\n"
            f"World model member: {member_name}\n"
            f"Onboarding complete: {onboarding}"
        )

    async def _set_name(self, ctx: SlashCommandContext, name: str) -> str:
        import json
        from datetime import datetime, timezone

        from sqlmodel import select

        from app.db import memory_session, users_session
        from app.models.memory import UserProfile
        from app.models.users import User
        from app.world.repository import WorldModelRepository

        now = datetime.now(timezone.utc)
        with users_session() as session:
            user = session.exec(select(User).where(User.id == ctx.user_id)).first()
            if not user:
                return "User not found."
            user.name = name
            user.onboarding_complete = True
            user.updated_at = now
            session.add(user)
            session.commit()

        WorldModelRepository.upsert_member(
            ctx.household_id,
            user_id=ctx.user_id,
            name=name,
            role="admin" if ctx.is_admin else "member",
            source="user_asserted",
            assert_name=True,
        )

        with memory_session() as session:
            profile = session.exec(
                select(UserProfile).where(UserProfile.user_id == ctx.user_id)
            ).first()
            if profile:
                try:
                    summary = json.loads(profile.summary or "{}")
                except Exception:
                    summary = {}
                summary["name"] = name
                profile.summary = json.dumps(summary)
                profile.updated_at = now
                session.add(profile)
                session.commit()

        return f"Updated your profile: {name}."

    async def _set_email(self, ctx: SlashCommandContext, address: str) -> str:
        import re

        from sqlmodel import select

        from app.db import users_session
        from app.models.users import ChannelMapping

        normalized = address.strip().lower()
        if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", normalized):
            return f"Invalid email address: {address}"

        with users_session() as session:
            conflict = session.exec(
                select(ChannelMapping).where(
                    ChannelMapping.channel == "email",
                    ChannelMapping.channel_user_id == normalized,
                )
            ).first()
            if conflict and conflict.user_id != ctx.user_id:
                return "That email address is already linked to another account. Ask an admin."

            existing = session.exec(
                select(ChannelMapping).where(
                    ChannelMapping.user_id == ctx.user_id,
                    ChannelMapping.channel == "email",
                    ChannelMapping.channel_user_id == normalized,
                )
            ).first()
            if not existing:
                session.add(
                    ChannelMapping(
                        user_id=ctx.user_id,
                        channel="email",
                        channel_user_id=normalized,
                    )
                )
                session.commit()

        return f"Email {normalized} linked to your account."

    async def _remove_email(self, ctx: SlashCommandContext, address: str) -> str:
        from sqlmodel import select

        from app.db import users_session
        from app.models.users import ChannelMapping

        normalized = address.strip().lower()
        with users_session() as session:
            mapping = session.exec(
                select(ChannelMapping).where(
                    ChannelMapping.user_id == ctx.user_id,
                    ChannelMapping.channel == "email",
                    ChannelMapping.channel_user_id == normalized,
                )
            ).first()
            if not mapping:
                return f"No email mapping found for {normalized}."
            session.delete(mapping)
            session.commit()

        return f"Email {normalized} removed."


# Register all commands in display order
for _cmd in [
    _Help(),
    _ContextStats(),
    _History(),
    _Schedule(),
    _ScheduledPrompts(),
    _Status(),
    _Users(),
    _Me(),
]:
    registry.register(_cmd)
