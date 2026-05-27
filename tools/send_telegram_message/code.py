"""Tool: send_telegram_message.

Sends a MarkdownV2-formatted message to the current user via the
module-level CommunicationBridge instance. Used by Reporter and CheckIn
for proactive delivery (inbound replies go through Scribe's pipeline).
"""

from __future__ import annotations

from google.adk.tools import ToolContext

from core.communication_bridge import active_bridge
from core.db import conversations, users


async def run(text: str, tool_context: ToolContext) -> dict:
    user_id = tool_context.state.get("user_id")
    if user_id is None:
        return {"ok": False, "error": "missing user_id in session state"}

    if not text or not text.strip():
        return {"ok": False, "error": "text is required"}

    bridge = active_bridge()
    if bridge is None:
        return {"ok": False, "error": "CommunicationBridge is not active"}

    telegram_id = users.get_telegram_id(user_id)
    if telegram_id is None:
        return {"ok": False, "error": f"no telegram_id for user_id {user_id}"}

    try:
        await bridge.send_message(telegram_id=telegram_id, text=text)
    except Exception as e:  # bridge already retries plain on parse fail; this catches network/etc.
        return {"ok": False, "error": f"send failed: {e}"}

    # Log proactive sends to the conversation history so Archivist/Profiler see them.
    conversations.append(user_id, "assistant", text)
    return {"ok": True, "message": "sent"}
