from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from telegram import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, MessageHandler, filters

from app.channels.base import Channel, MediaAttachment
from app.config import get_settings

logger = logging.getLogger(__name__)

_MAX_MEDIA_BYTES = 10 * 1024 * 1024  # 10 MB
_TG_MAX_CHARS = 4096  # Telegram sendMessage hard limit


def _split_message(text: str) -> list[str]:
    """Split text into chunks that fit within Telegram's 4096-char limit.

    Tries to break on paragraph boundaries, then line boundaries,
    then hard-cuts as a last resort.
    """
    if len(text) <= _TG_MAX_CHARS:
        return [text]

    chunks: list[str] = []
    remaining = text
    while remaining:
        if len(remaining) <= _TG_MAX_CHARS:
            chunks.append(remaining)
            break
        # Prefer paragraph break
        cut = remaining.rfind("\n\n", 0, _TG_MAX_CHARS)
        if cut == -1:
            # Fall back to single newline
            cut = remaining.rfind("\n", 0, _TG_MAX_CHARS)
        if cut == -1:
            # Hard cut
            cut = _TG_MAX_CHARS
        else:
            cut += 1  # keep the newline with the preceding chunk
        chunks.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip()

    return [c for c in chunks if c]


# Callback type: (telegram_id, text, attachments) → response string or None
MessageCallback = Callable[[int, str, list[MediaAttachment]], Awaitable[str | None]]


class TelegramChannel(Channel):
    def __init__(self, token: str, on_message: MessageCallback) -> None:
        self._app = Application.builder().token(token).build()
        self._on_message = on_message
        self._register_handlers()

    # ------------------------------------------------------------------
    # Handler registration
    # ------------------------------------------------------------------

    def _register_handlers(self) -> None:
        self._app.add_handler(MessageHandler(filters.TEXT | filters.COMMAND, self._handle_message))
        self._app.add_handler(
            MessageHandler(filters.PHOTO | filters.VOICE | filters.AUDIO, self._handle_media)
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

        response = await self._on_message(telegram_id, text, [])
        if response:
            for chunk in _split_message(response):
                await update.message.reply_text(chunk)

    async def _handle_media(self, update: Update, context: object) -> None:
        if not update.effective_user or not update.message:
            return

        telegram_id = update.effective_user.id
        settings = get_settings()
        if telegram_id not in settings.allowed_telegram_ids:
            return  # silent drop

        msg = update.message
        caption = msg.caption or ""

        # Determine file_id, file_size, and mime_type
        if msg.photo:
            # photo is a list of PhotoSize; last entry is the largest
            photo = msg.photo[-1]
            file_id = photo.file_id
            file_size = photo.file_size or 0
            mime_type = "image/jpeg"
        elif msg.voice:
            file_id = msg.voice.file_id
            file_size = msg.voice.file_size or 0
            mime_type = "audio/ogg"
        elif msg.audio:
            file_id = msg.audio.file_id
            file_size = msg.audio.file_size or 0
            mime_type = msg.audio.mime_type or "audio/mpeg"
        else:
            return

        if file_size > _MAX_MEDIA_BYTES:
            await msg.reply_text("Sorry, that file is too large to process (max 10 MB).")
            return

        try:
            from telegram import Bot

            bot: Bot = self._app.bot
            tg_file = await bot.get_file(file_id)
            ba = await tg_file.download_as_bytearray()
            attachments = [MediaAttachment(data=bytes(ba), mime_type=mime_type)]
        except Exception:
            logger.exception("Failed to download media (file_id=%s)", file_id)
            await msg.reply_text("Sorry, I couldn't download that file. Please try again.")
            return

        logger.info(
            "Media received: mime=%s size=%d caption=%r telegram_id=%d",
            mime_type,
            len(ba),
            caption,
            telegram_id,
        )
        response = await self._on_message(telegram_id, caption, attachments)
        if response:
            for chunk in _split_message(response):
                await msg.reply_text(chunk)

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
            token = data[len("confirm:") :]
            await self._execute_confirmed_action(query, token, telegram_id)
        elif data.startswith("cancel:"):
            token = data[len("cancel:") :]
            await self._cancel_pending_action(query, token, telegram_id)
        elif data.startswith("email_confirm:"):
            token = data[len("email_confirm:") :]
            await self._execute_email_intake(query, token, telegram_id)
        elif data.startswith("email_cancel:"):
            token = data[len("email_cancel:") :]
            await self._cancel_email_intake(query, token, telegram_id)
        else:
            await query.answer("Unknown action")

    async def _execute_confirmed_action(
        self, query: CallbackQuery, token: str, telegram_id: int
    ) -> None:
        from app.policy.confirm import execute_pending_action

        user_id = await self._resolve_user_id(telegram_id)
        if user_id is None:
            await query.answer("This action doesn't belong to you.")
            return

        await query.answer("Executing…")
        result = await execute_pending_action(token, user_id, str(telegram_id), channel="telegram")
        icon = "✅" if result.ok else ("⚠️" if result.status == "expired" else "❌")
        await query.edit_message_text(f"{icon} {result.message}")

    async def _cancel_pending_action(
        self, query: CallbackQuery, token: str, telegram_id: int
    ) -> None:
        from app.policy.confirm import cancel_pending_action_for_user

        user_id = await self._resolve_user_id(telegram_id)
        if user_id is None:
            await query.answer("This action doesn't belong to you.")
            return

        result = await cancel_pending_action_for_user(token, user_id)
        await query.answer("Cancelled" if result.status == "cancelled" else None)
        icon = "❌" if result.status == "cancelled" else "⚠️"
        await query.edit_message_text(f"{icon} {result.message}")

    async def _resolve_user_id(self, telegram_id: int) -> str | None:
        """Return the internal User.id for a Telegram user, or None if unknown."""
        from sqlmodel import select

        from app.db import users_session
        from app.models.users import User

        with users_session() as session:
            user = session.exec(select(User).where(User.telegram_id == telegram_id)).first()
        return user.id if user else None

    async def _action_belongs_to(self, telegram_id: int, action_user_id: str) -> bool:
        """Return True if the Telegram user owns the given PendingAction."""
        user_id = await self._resolve_user_id(telegram_id)
        return user_id is not None and user_id == action_user_id

    async def _execute_email_intake(
        self, query: CallbackQuery, token: str, telegram_id: int
    ) -> None:
        from app.email.confirmation import delete_confirmation, get_confirmation

        confirmation = get_confirmation(token)
        if confirmation is None:
            await query.answer()
            await query.edit_message_text("⚠️ This email action has expired or was already handled.")
            return

        if not await self._action_belongs_to(telegram_id, confirmation.user_id):
            await query.answer("This action doesn't belong to you.")
            return

        delete_confirmation(token)
        await query.answer("Processing…")
        await query.edit_message_text("✅ Got it — processing your email now.")

        from app.agent.runner import agent_run

        try:
            outcome = await agent_run(
                text=confirmation.intake_text,
                user_id=confirmation.user_id,
                household_id=confirmation.household_id,
                channel_user_id=str(telegram_id),
                trigger="email_confirmed",
                save_history=False,
            )
            if outcome.response:
                await self.send_message(str(telegram_id), outcome.response)
        except Exception:
            logger.exception("email_intake agent_run failed (token=%s)", token)
            await self.send_message(
                str(telegram_id), "⚠️ Something went wrong processing your email."
            )

        from sqlmodel import select

        from app.db import cache_session
        from app.email.models import EmailMessage
        from app.email.reply import send_ack_reply
        from app.email.repository import update_status

        with cache_session() as session:
            msg = session.exec(
                select(EmailMessage).where(EmailMessage.confirmation_id == token)
            ).first()
            if msg:
                update_status(msg.id, "PROCESSED")
                asyncio.ensure_future(
                    send_ack_reply(msg.provider_inbox_id, msg.provider_message_id, msg.from_email)
                )

    async def _cancel_email_intake(
        self, query: CallbackQuery, token: str, telegram_id: int
    ) -> None:
        from app.email.confirmation import delete_confirmation, get_confirmation

        confirmation = get_confirmation(token)
        if confirmation is None:
            await query.answer()
            await query.edit_message_text("⚠️ This email action has expired or was already handled.")
            return

        if not await self._action_belongs_to(telegram_id, confirmation.user_id):
            await query.answer("This action doesn't belong to you.")
            return

        await query.answer("Skipped")
        delete_confirmation(token)
        await query.edit_message_text("⏭️ Email skipped.")

        from sqlmodel import select

        from app.db import cache_session
        from app.email.models import EmailMessage
        from app.email.repository import update_status

        with cache_session() as session:
            msg = session.exec(
                select(EmailMessage).where(EmailMessage.confirmation_id == token)
            ).first()
            if msg:
                update_status(msg.id, "IGNORED", status_reason="user_skipped")

    # ------------------------------------------------------------------
    # Channel interface
    # ------------------------------------------------------------------

    async def send_message(self, channel_user_id: str, text: str) -> None:
        for chunk in _split_message(text):
            await self._app.bot.send_message(chat_id=int(channel_user_id), text=chunk)

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

    async def send_email_intake_prompt(
        self,
        channel_user_id: str,
        prompt_text: str,
        token: str,
    ) -> None:
        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("✅ Yes", callback_data=f"email_confirm:{token}"),
                    InlineKeyboardButton("⏭️ Skip", callback_data=f"email_cancel:{token}"),
                ]
            ]
        )
        await self._app.bot.send_message(
            chat_id=int(channel_user_id),
            text=prompt_text,
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
