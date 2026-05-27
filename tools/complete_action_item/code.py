"""Tool: complete_action_item.

Marks an action item completed only if it belongs to the calling user.
"""

from __future__ import annotations

from google.adk.tools import ToolContext

from core.db import action_items


def run(action_item_id: int, tool_context: ToolContext) -> dict:
    user_id = tool_context.state.get("user_id")
    if user_id is None:
        return {"ok": False, "error": "missing user_id in session state"}

    updated = action_items.mark_completed(item_id=action_item_id, user_id=user_id)
    if not updated:
        return {
            "ok": False,
            "error": f"action item #{action_item_id} not found or not owned by current user",
        }

    return {
        "ok": True,
        "action_item_id": action_item_id,
        "message": f"Completed task #{action_item_id}",
    }
