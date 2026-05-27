"""Tool: list_schedules."""

from __future__ import annotations

from google.adk.tools import ToolContext

from core.db import scheduled_tasks


def run(tool_context: ToolContext, include_inactive: bool = False) -> dict:
    user_id = tool_context.state.get("user_id")
    if user_id is None:
        return {"ok": False, "error": "missing user_id in session state"}

    rows = scheduled_tasks.list_for_user(user_id, only_active=not include_inactive)
    return {"ok": True, "count": len(rows), "schedules": rows}
