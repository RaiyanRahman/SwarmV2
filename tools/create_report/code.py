"""Tool: create_report.

Creates a new user-owned report template. The caller (Reporter or Orchestrator)
should chain this with register_schedule to actually fire it on a cron.
"""

from __future__ import annotations

import sqlite3

from google.adk.tools import ToolContext

from core.db import reports


def run(
    name: str,
    style_instructions: str,
    data_criteria: str,
    tool_context: ToolContext,
) -> dict:
    user_id = tool_context.state.get("user_id")
    if user_id is None:
        return {"ok": False, "error": "missing user_id in session state"}

    if not name or not name.strip():
        return {"ok": False, "error": "name is required"}
    if not style_instructions or not style_instructions.strip():
        return {"ok": False, "error": "style_instructions is required"}
    if not data_criteria or not data_criteria.strip():
        return {"ok": False, "error": "data_criteria is required"}

    try:
        report_id = reports.create(
            user_id=user_id,
            name=name.strip(),
            style_instructions=style_instructions.strip(),
            data_criteria=data_criteria.strip(),
            is_default=False,
        )
    except sqlite3.IntegrityError:
        return {
            "ok": False,
            "error": f"a report named {name.strip()!r} already exists for this user",
        }

    return {
        "ok": True,
        "report_id": report_id,
        "name": name.strip(),
        "message": f"Created report #{report_id}: {name.strip()}",
    }
