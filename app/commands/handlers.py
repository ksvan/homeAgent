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
        from datetime import datetime, timezone as _utc

        from app.agent.context import assemble_context
        from app.config import get_settings

        assembled = assemble_context(ctx.user_id, ctx.household_id, ctx.raw_text)

        msg_chars = sum(
            len(str(getattr(part, "content", "")))
            for msg in assembled.recent_messages
            for part in msg.parts
        )
        summary_chars = len(assembled.conversation_summary or "")
        user_profile_chars = len(assembled.user_profile_text)
        household_profile_chars = len(assembled.household_profile_text)
        mem_count = len(assembled.relevant_memories)
        mem_chars = sum(len(m) for m in assembled.relevant_memories)
        total_chars = (
            msg_chars + summary_chars + user_profile_chars + household_profile_chars + mem_chars
        )
        approx_tokens = total_chars // 4

        settings = get_settings()
        try:
            from zoneinfo import ZoneInfo
            tz = ZoneInfo(settings.household_timezone)
        except Exception:
            tz = _utc.utc
        now = datetime.now(tz)
        offset = now.strftime("%z")
        utc_offset = f"{offset[:3]}:{offset[3:]}"
        date_str = now.strftime("%A, %d %B %Y")
        time_str = now.strftime("%H:%M") + f" (UTC{utc_offset})"

        return (
            f"Context breakdown:\n\n"
            f"  Date/time in ctx : {date_str}, {time_str}\n"
            f"  Recent messages  : {len(assembled.recent_messages)} ({msg_chars:,} chars)\n"
            f"  Summary          : {summary_chars:,} chars\n"
            f"  User profile     : {user_profile_chars:,} chars\n"
            f"  Household profile: {household_profile_chars:,} chars\n"
            f"  Memories         : {mem_count} ({mem_chars:,} chars)\n"
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
        footer = (
            "\n\n[Older messages are covered by a conversation summary.]" if summary else ""
        )
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
            task_data: dict = {}
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

            if len(desc) > 60:
                desc = desc[:60] + "…"
            lines.append(f"[{kind}] {scheduled_at}  {desc}  (id: {task.id[:8]})")

        return f"{len(tasks)} scheduled item(s):\n\n" + "\n".join(lines)


class _Status(SlashCommand):
    name = "status"
    help = "Operational status snapshot"
    admin_only = True

    async def run(self, ctx: SlashCommandContext) -> str:
        from app.homey.mcp_client import get_mcp_server as get_homey
        from app.prometheus.mcp_client import get_mcp_server as get_prom
        from app.scheduler.engine import get_scheduler

        def _mark(ok: bool) -> str:
            return "ok" if ok else "unavailable"

        lines = [
            f"Scheduler       : {_mark(get_scheduler() is not None)}",
            f"Homey MCP       : {_mark(get_homey() is not None)}",
            f"Prometheus MCP  : {_mark(get_prom() is not None)}",
        ]
        return "Status:\n\n" + "\n".join(lines)


class _Users(SlashCommand):
    name = "users"
    help = "List household members"
    admin_only = True

    async def run(self, ctx: SlashCommandContext) -> str:
        from sqlmodel import select

        from app.db import users_session
        from app.models.users import User

        with users_session() as session:
            users = session.exec(
                select(User).where(User.household_id == ctx.household_id)
            ).all()

        if not users:
            return "No users found."

        lines = []
        for u in users:
            admin_tag = "  [admin]" if u.is_admin else ""
            lines.append(f"{u.name}  (tg: {u.telegram_id}){admin_tag}")

        return f"{len(users)} user(s):\n\n" + "\n".join(lines)


# Register all commands in display order
for _cmd in [_Help(), _ContextStats(), _History(), _Schedule(), _Status(), _Users()]:
    registry.register(_cmd)
