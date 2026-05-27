"""Tool: list_action_items.

Returns the calling user's action items, ordered by priority then target_date.
Items are scoped to user_id from tool_context.state — the LLM cannot list
another user's tasks.
"""

from __future__ import annotations

from typing import Optional

from google.adk.tools import ToolContext

from core.db import action_items


def run(
    tool_context: ToolContext,
    priority: Optional[str] = None,
    include_completed: bool = False,
) -> dict:
    user_id = tool_context.state.get("user_id")
    if user_id is None:
        return {"ok": False, "error": "missing user_id in session state"}

    if priority is not None and priority not in action_items.VALID_PRIORITIES:
        return {"ok": False, "error": f"invalid priority {priority!r}"}

    items = action_items.list_for_user(
        user_id=user_id,
        priority=priority,
        include_completed=include_completed,
    )
    return {
        "ok": True,
        "count": len(items),
        "items": items,
    }
