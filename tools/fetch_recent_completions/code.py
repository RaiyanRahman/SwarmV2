"""Tool: fetch_recent_completions."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from google.adk.tools import ToolContext

from core.db import action_items


def run(tool_context: ToolContext, hours: int = 24) -> dict:
    user_id = tool_context.state.get("user_id")
    if user_id is None:
        return {"ok": False, "error": "missing user_id in session state"}
    if hours <= 0:
        return {"ok": False, "error": "hours must be positive"}

    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    items = action_items.list_completed_since(user_id=user_id, since_iso=since.isoformat())
    return {"ok": True, "count": len(items), "items": items}
