"""Tool: register_schedule.

Inserts a scheduled_tasks row for the current user and signals the live
Scheduler to reload. Validates the cron expression at the boundary so a
malformed expression from the LLM never enters the DB.
"""

from __future__ import annotations

from typing import Optional

from apscheduler.triggers.cron import CronTrigger
from google.adk.tools import ToolContext

from core.db import scheduled_tasks, reports
from core.scheduler import active_scheduler


def run(
    cron_expression: str,
    prompt_payload: str,
    tool_context: ToolContext,
    report_id: Optional[int] = None,
) -> dict:
    user_id = tool_context.state.get("user_id")
    if user_id is None:
        return {"ok": False, "error": "missing user_id in session state"}

    if not cron_expression or not cron_expression.strip():
        return {"ok": False, "error": "cron_expression is required"}
    try:
        CronTrigger.from_crontab(cron_expression.strip())
    except Exception as e:
        return {"ok": False, "error": f"invalid cron expression: {e}"}

    if not prompt_payload or not prompt_payload.strip():
        return {"ok": False, "error": "prompt_payload is required"}

    if report_id is not None:
        report = reports.get(report_id)
        if report is None or report["user_id"] != user_id:
            return {
                "ok": False,
                "error": f"report_id {report_id} not found or not owned by current user",
            }

    task_id = scheduled_tasks.create(
        user_id=user_id,
        cron_expression=cron_expression.strip(),
        prompt_payload=prompt_payload.strip(),
        report_id=report_id,
    )

    sched = active_scheduler()
    if sched is not None:
        sched.reload_from_db()

    return {
        "ok": True,
        "schedule_id": task_id,
        "cron_expression": cron_expression.strip(),
        "message": f"Scheduled task #{task_id} ({cron_expression.strip()})",
    }
