"""CommunicationBridge — the Telegram boundary.

Owns the python-telegram-bot Application and exposes send/listen primitives.
Scribe receives this object as a tool dependency rather than importing PTB
directly, so the agent layer stays transport-agnostic.
"""

from __future__ import annotations

import logging
from typing import Awaitable, Callable, Optional

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    ApplicationBuilder,
    ContextTypes,
    MessageHandler,
    filters,
)

log = logging.getLogger(__name__)

# Type alias: callback the bridge invokes when a Telegram message arrives.
# It receives the raw text, the telegram_id, and PTB's per-chat user_data dict.
InboundHandler = Callable[[str, str, dict], Awaitable[Optional[str]]]


class CommunicationBridge:
    """Telegram transport. One instance per process."""

    def __init__(
        self,
        token: str,
        allowed_telegram_ids: Optional[set[str]] = None,
    ) -> None:
        self._token = token
        self._allowed = {str(x) for x in allowed_telegram_ids} if allowed_telegram_ids else None
        self._inbound: Optional[InboundHandler] = None
        self._app: Optional[Application] = None

    # lifecycle -------------------------------------------------------------
    def build(self) -> Application:
        """Construct the PTB Application and wire the message handler."""
        app = ApplicationBuilder().token(self._token).build()
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self._on_message))
        self._app = app
        return app

    def set_inbound_handler(self, handler: InboundHandler) -> None:
        """Register the callback that processes user messages."""
        self._inbound = handler

    # outbound --------------------------------------------------------------
    async def send_message(
        self,
        telegram_id: str,
        text: str,
        parse_mode: Optional[str] = ParseMode.MARKDOWN,
    ) -> None:
        """Send text to a user by Telegram id. Safe to call from any agent/tool."""
        if self._app is None:
            raise RuntimeError("CommunicationBridge.build() must be called first")
        try:
            await self._app.bot.send_message(
                chat_id=int(telegram_id), text=text, parse_mode=parse_mode
            )
        except Exception:
            # Markdown parse failures are the most common error here — retry plain.
            log.exception("send_message failed with parse_mode=%s, retrying plain", parse_mode)
            await self._app.bot.send_message(chat_id=int(telegram_id), text=text)

    # inbound ---------------------------------------------------------------
    async def _on_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if update.message is None or update.effective_user is None:
            return
        telegram_id = str(update.effective_user.id)
        text = update.message.text or ""

        if self._allowed is not None and telegram_id not in self._allowed:
            log.warning("Rejected message from non-allowlisted telegram_id=%s", telegram_id)
            await update.message.reply_text("Sorry, you're not authorized to use this bot.")
            return

        if self._inbound is None:
            log.error("Inbound handler not registered; dropping message")
            return

        try:
            reply = await self._inbound(text, telegram_id, context.user_data)
        except Exception:
            log.exception("Inbound handler crashed for telegram_id=%s", telegram_id)
            await update.message.reply_text("Something went wrong. The error has been logged.")
            return

        if reply:
            try:
                await update.message.reply_text(reply, parse_mode=ParseMode.MARKDOWN)
            except Exception:
                log.exception("Markdown reply failed, falling back to plain")
                await update.message.reply_text(reply)
