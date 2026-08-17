from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

logger = logging.getLogger(__name__)


class TelegramNotifier:
    """Learns its target chat via /start, then pushes new-listing notifications to it."""

    def __init__(self, token: str) -> None:
        self._app = Application.builder().token(token).build()
        self._app.add_handler(CommandHandler("start", self._on_start))
        self._chat_id: int | None = None

    async def _on_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        self._chat_id = update.effective_chat.id
        await update.message.reply_text("Started — you'll get a message here when a new listing appears.")

    async def start(self) -> None:
        await self._app.initialize()
        await self._app.start()
        await self._app.updater.start_polling()

    async def stop(self) -> None:
        await self._app.updater.stop()
        await self._app.stop()
        await self._app.shutdown()

    async def notify_new_listing(self, name: str, link: str) -> None:
        if self._chat_id is None:
            logger.warning("New listing found but no chat is known yet (send /start to the bot): %s", name)
            return
        await self._app.bot.send_message(chat_id=self._chat_id, text=f"New listing: {name}\n{link}")
