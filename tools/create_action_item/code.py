"""Tool: create_action_item.

Inserts a new row into action_items. Validates priority and title at the
boundary — internal callers are trusted, but the LLM is not.
"""

from __future__ import annotations

from typing import Optional

from core.db import action_items, users


def run(
    user_id: int,
    title: str,
    priority: str,
    content_details: Optional[str] = None,
    target_date: Optional[str] = None,
) -> dict:
    """Create an action item and return its id + a confirmation.

    Returns a dict so the LLM can format a reply with the new id (and a future
    edit/complete flow can reference it via state_manager.LAST_ACTION_ITEM_ID).
    """
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

    return {
        "ok": True,
        "action_item_id": item_id,
        "title": title,
        "priority": priority,
        "message": f"Created {priority} task #{item_id}: {title}",
    }
