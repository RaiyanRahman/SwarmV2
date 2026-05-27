"""Tool: update_action_item.

Updates only the provided fields of an action item, scoped to the current user.
"""

from __future__ import annotations

from typing import Optional

from google.adk.tools import ToolContext

from core.db import action_items


def run(
    action_item_id: int,
    tool_context: ToolContext,
    title: Optional[str] = None,
    content_details: Optional[str] = None,
    priority: Optional[str] = None,
    target_date: Optional[str] = None,
) -> dict:
    user_id = tool_context.state.get("user_id")
    if user_id is None:
        return {"ok": False, "error": "missing user_id in session state"}

    if all(v is None for v in (title, content_details, priority, target_date)):
        return {"ok": False, "error": "at least one field must be supplied to update"}

    # Empty-string target_date clears the deadline.
    td_param = None if target_date is None else (None if target_date == "" else target_date)

    try:
        updated = action_items.update(
            item_id=action_item_id,
            user_id=user_id,
            title=title,
            content_details=content_details,
            priority=priority,
            target_date=td_param,
        )
    except ValueError as e:
        return {"ok": False, "error": str(e)}

    if not updated:
        return {
            "ok": False,
            "error": f"action item #{action_item_id} not found or not owned by current user",
        }

    return {
        "ok": True,
        "action_item_id": action_item_id,
        "message": f"Updated task #{action_item_id}",
    }
