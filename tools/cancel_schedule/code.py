"""Tool: cancel_schedule."""

from __future__ import annotations

from google.adk.tools import ToolContext

from core.db import scheduled_tasks
from core.scheduler import active_scheduler


def run(schedule_id: int, tool_context: ToolContext) -> dict:
    user_id = tool_context.state.get("user_id")
    if user_id is None:
        return {"ok": False, "error": "missing user_id in session state"}

    changed = scheduled_tasks.set_active(schedule_id, active=False, user_id=user_id)
    if not changed:
        return {
            "ok": False,
            "error": f"schedule #{schedule_id} not found or not owned by current user",
        }

    sched = active_scheduler()
    if sched is not None:
        sched.reload_from_db()

    return {
        "ok": True,
        "schedule_id": schedule_id,
        "message": f"Cancelled schedule #{schedule_id}",
    }
