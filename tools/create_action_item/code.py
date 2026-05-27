"""Tool: create_action_item.

Inserts a new row into action_items. The LLM does NOT supply user_id —
it's read from tool_context.state, which the Orchestrator seeds once per
run. This removes a class of hallucination/permission bugs and lets the
tool schema stay minimal in tool.json.
"""

from __future__ import annotations

from typing import Optional

from google.adk.tools import ToolContext

from core.db import action_items, users


def run(
    title: str,
    priority: str,
    tool_context: ToolContext,
    content_details: Optional[str] = None,
    target_date: Optional[str] = None,
) -> dict:
    """Create an action item and return its id + a confirmation."""
    user_id = tool_context.state.get("user_id")
    if user_id is None:
        return {"ok": False, "error": "missing user_id in session state"}

    if users.get_by_id(user_id) is None:
        return {"ok": False, "error": f"unknown user_id {user_id}"}

    try:
        item_id = action_items.create(
            user_id=user_id,
            title=title,
            priority=priority,
            content_details=content_details,
            target_date=target_date,
        )
    except ValueError as e:
        return {"ok": False, "error": str(e)}

    # Surface the new id back to the session so the next agent in the chain
    # (or a later tool call within the same turn) can reference it.
    tool_context.state["LAST_ACTION_ITEM_ID"] = item_id

    return {
        "ok": True,
        "action_item_id": item_id,
        "title": title,
        "priority": priority,
        "message": f"Created {priority} task #{item_id}: {title}",
    }
