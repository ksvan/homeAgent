from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable

from telegram import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, MessageHandler, filters

from app.channels.base import Channel
from app.config import get_settings

logger = logging.getLogger(__name__)

# Callback type: (telegram_id, text) → response string or None
MessageCallback = Callable[[int, str], Awaitable[str | None]]


class TelegramChannel(Channel):
    def __init__(self, token: str, on_message: MessageCallback) -> None:
        self._app = Application.builder().token(token).build()
        self._on_message = on_message
        self._register_handlers()

    # ------------------------------------------------------------------
    # Handler registration
    # ------------------------------------------------------------------

    def _register_handlers(self) -> None:
        self._app.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self._handle_message)
        )
        self._app.add_handler(CallbackQueryHandler(self._handle_callback_query))

    # ------------------------------------------------------------------
    # Incoming update handlers
    # ------------------------------------------------------------------

    async def _handle_message(self, update: Update, _context: object) -> None:
        if not update.effective_user or not update.message or not update.message.text:
            return

        telegram_id = update.effective_user.id
        text = update.message.text

        settings = get_settings()
        if telegram_id not in settings.allowed_telegram_ids:
            return  # silent drop

        response = await self._on_message(telegram_id, text)
        if response:
            await update.message.reply_text(response)

    async def _handle_callback_query(self, update: Update, _context: object) -> None:
        """Handle Yes/No confirmation button presses from the Policy Gate."""
        query = update.callback_query
        if not query or not query.data or not query.from_user:
            return

        telegram_id = query.from_user.id
        settings = get_settings()
        if telegram_id not in settings.allowed_telegram_ids:
            await query.answer()
            return

        data: str = query.data
        if data.startswith("confirm:"):
            token = data[len("confirm:"):]
            await self._execute_confirmed_action(query, token, telegram_id)
        elif data.startswith("cancel:"):
            token = data[len("cancel:"):]
            await self._cancel_pending_action(query, token, telegram_id)
        else:
            await query.answer("Unknown action")

    async def _execute_confirmed_action(
        self, query: CallbackQuery, token: str, telegram_id: int
    ) -> None:
        from app.homey.mcp_client import get_mcp_server
        from app.policy.pending import delete_pending_action, get_pending_action

        action = get_pending_action(token)
        if action is None:
            await query.answer()
            await query.edit_message_text("⚠️ This action has expired or was already handled.")
            return

        if not await self._action_belongs_to(telegram_id, action.user_id):
            await query.answer("This action doesn't belong to you.")
            return

        await query.answer("Executing…")

        server = get_mcp_server()
        if server is None:
            await query.edit_message_text("⚠️ Homey is not connected — cannot execute.")
            delete_pending_action(token)
            return

        from app.memory.conversation import save_message_pair

        try:
            tool_args: dict[str, object] = json.loads(action.tool_args)
            result = await server.direct_call_tool(action.tool_name, tool_args, None)
            delete_pending_action(token)

            await query.edit_message_text(f"✅ Done: {result}")
            logger.info(
                "Confirmed action executed: %s (token=%s)", action.tool_name, token
            )

            # Persist to conversation history so the agent doesn't re-prompt next message
            save_message_pair(
                action.user_id,
                "[User confirmed action via Telegram button]",
                f"The action '{action.tool_name}' was confirmed by the user and executed successfully. No further confirmation is needed.",
            )

            # Schedule state verification
            from app.homey.verify import verify_after_write

            asyncio.ensure_future(
                verify_after_write(
                    action.household_id, str(telegram_id), action.tool_name, tool_args
                )
            )
        except Exception:
            logger.exception("Failed to execute confirmed action (token=%s)", token)
            delete_pending_action(token)
            await query.edit_message_text(
                "❌ Action failed — please check the device and try again."
            )
            # Persist failure so the agent doesn't keep re-prompting for the same action
            save_message_pair(
                action.user_id,
                "[User confirmed action via Telegram button — action failed]",
                f"The action '{action.tool_name}' was confirmed by the user but failed to execute. The user has been notified. Do not retry this action automatically.",
            )

    async def _cancel_pending_action(
        self, query: CallbackQuery, token: str, telegram_id: int
    ) -> None:
        from app.policy.pending import delete_pending_action, get_pending_action

        action = get_pending_action(token)
        if action is None:
            await query.answer()
            await query.edit_message_text("⚠️ This action has expired or was already handled.")
            return

        if not await self._action_belongs_to(telegram_id, action.user_id):
            await query.answer("This action doesn't belong to you.")
            return

        await query.answer("Cancelled")
        delete_pending_action(token)
        await query.edit_message_text("❌ Action cancelled.")
        logger.info("Pending action cancelled (token=%s)", token)

    async def _action_belongs_to(self, telegram_id: int, action_user_id: str) -> bool:
        """Return True if the Telegram user owns the given PendingAction."""
        from sqlmodel import select

        from app.db import users_session
        from app.models.users import User

        with users_session() as session:
            user = session.exec(
                select(User).where(User.telegram_id == telegram_id)
            ).first()
        return user is not None and user.id == action_user_id

    # ------------------------------------------------------------------
    # Channel interface
    # ------------------------------------------------------------------

    async def send_message(self, channel_user_id: str, text: str) -> None:
        await self._app.bot.send_message(chat_id=int(channel_user_id), text=text)

    async def send_confirmation_prompt(
        self,
        channel_user_id: str,
        action_description: str,
        token: str,
    ) -> None:
        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("✅ Yes", callback_data=f"confirm:{token}"),
                    InlineKeyboardButton("❌ No", callback_data=f"cancel:{token}"),
                ]
            ]
        )
        await self._app.bot.send_message(
            chat_id=int(channel_user_id),
            text=f"Confirm action: {action_description}",
            reply_markup=keyboard,
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start_polling(self) -> None:
        """Run in development mode — blocks until interrupted."""
        logger.info("Starting Telegram polling")
        await self._app.initialize()
        await self._app.start()
        assert self._app.updater is not None
        await self._app.updater.start_polling()
        try:
            await asyncio.Event().wait()
        except (KeyboardInterrupt, asyncio.CancelledError):
            pass
        finally:
            for _coro in (
                self._app.updater.stop(),
                self._app.stop(),
                self._app.shutdown(),
            ):
                try:
                    await _coro
                except (asyncio.CancelledError, Exception):
                    pass

    async def initialize(self) -> None:
        """Initialize for webhook mode (called in FastAPI lifespan startup)."""
        await self._app.initialize()
        await self._app.start()

    async def shutdown(self) -> None:
        """Shutdown for webhook mode (called in FastAPI lifespan shutdown)."""
        await self._app.stop()
        await self._app.shutdown()

    async def process_update(self, data: dict[str, object]) -> None:
        """Process a raw JSON update from the webhook endpoint."""
        update = Update.de_json(data, self._app.bot)
        if update:
            await self._app.process_update(update)
