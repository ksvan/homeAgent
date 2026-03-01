from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
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
        self._app.add_handler(
            CallbackQueryHandler(self._handle_callback_query)
        )

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
        """Handle Yes/No confirmation button presses (Policy Gate — M5)."""
        if not update.callback_query:
            return
        # Stub: full implementation in Milestone 5 (Policy Gate)
        await update.callback_query.answer("Not yet implemented")

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
            await self._app.updater.stop()
            await self._app.stop()
            await self._app.shutdown()

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
