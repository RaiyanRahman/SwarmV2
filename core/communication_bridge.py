"""CommunicationBridge — the Telegram boundary.

Owns the python-telegram-bot Application and exposes send/listen primitives.
Scribe receives this object as a tool dependency rather than importing PTB
directly, so the agent layer stays transport-agnostic.

Markdown handling
-----------------
Outbound text is sent with ParseMode.MARKDOWN_V2, the richer of Telegram's
two Markdown dialects. MarkdownV2 supports:
  *bold*, _italic_, __underline__, ~strike~, ||spoiler||, `code`,
  ```pre```, [text](url), and > blockquote.

MarkdownV2 also reserves these characters and they MUST be escaped outside
of formatting entities:  _ * [ ] ( ) ~ ` > # + - = | { } . !

Two escape helpers are exported:
  - escape_for_v2(text): escapes every reserved char. Use for arbitrary
    user/data text that should appear verbatim with no formatting.
  - escape_inside_code(text): escapes ` and \\ only. Use for content
    placed inside a `code` or ```pre``` block.

If the LLM emits text that fails to parse, we retry once as plain text
(parse_mode=None) so a malformed prompt never silently drops a message.
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
from telegram.helpers import escape_markdown

log = logging.getLogger(__name__)

# Type alias: callback the bridge invokes when a Telegram message arrives.
# It receives the raw text, the telegram_id, and PTB's per-chat user_data dict.
InboundHandler = Callable[[str, str, dict], Awaitable[Optional[str]]]


def escape_for_v2(text: str) -> str:
    """Escape every MarkdownV2 reserved char in raw text."""
    return escape_markdown(text, version=2)


def escape_inside_code(text: str) -> str:
    """Escape only the chars meaningful inside a code/pre block: backtick and backslash."""
    return text.replace("\\", "\\\\").replace("`", "\\`")


# Module-level registration so tools (send_telegram_message) can reach the
# live bridge without dependency injection.
_active_bridge: Optional["CommunicationBridge"] = None


def set_active_bridge(bridge: "CommunicationBridge") -> None:
    global _active_bridge
    _active_bridge = bridge


def active_bridge() -> Optional["CommunicationBridge"]:
    return _active_bridge


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
        parse_mode: Optional[str] = ParseMode.MARKDOWN_V2,
    ) -> None:
        """Send text to a user. Falls back to plain on MarkdownV2 parse failure."""
        if self._app is None:
            raise RuntimeError("CommunicationBridge.build() must be called first")
        try:
            await self._app.bot.send_message(
                chat_id=int(telegram_id), text=text, parse_mode=parse_mode
            )
        except Exception:
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
                await update.message.reply_text(reply, parse_mode=ParseMode.MARKDOWN_V2)
            except Exception:
                log.exception("MarkdownV2 reply failed, falling back to plain")
                await update.message.reply_text(reply)
