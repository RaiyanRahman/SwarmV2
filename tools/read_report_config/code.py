"""Tool: read_report_config."""

from __future__ import annotations

from google.adk.tools import ToolContext

from core.db import reports


def run(report_id: int, tool_context: ToolContext) -> dict:
    user_id = tool_context.state.get("user_id")
    if user_id is None:
        return {"ok": False, "error": "missing user_id in session state"}

    row = reports.get(report_id)
    if row is None or row["user_id"] != user_id:
        return {
            "ok": False,
            "error": f"report_id {report_id} not found or not owned by current user",
        }
    return {"ok": True, "report": row}
