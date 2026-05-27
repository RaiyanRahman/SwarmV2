"""Tool: fetch_tasks_due_today."""

from __future__ import annotations

from datetime import datetime, time, timezone

from google.adk.tools import ToolContext

from core.db import action_items


def run(tool_context: ToolContext) -> dict:
    user_id = tool_context.state.get("user_id")
    if user_id is None:
        return {"ok": False, "error": "missing user_id in session state"}

    # End of today in UTC. We accept overdue items too (target_date <= end-of-today).
    today_end = datetime.combine(datetime.now(timezone.utc).date(), time.max, tzinfo=timezone.utc)
    items = action_items.list_due_by(user_id=user_id, before_iso=today_end.isoformat())
    return {"ok": True, "count": len(items), "items": items}
