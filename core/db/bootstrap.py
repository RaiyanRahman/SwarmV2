"""Per-user default seeding.

Runs idempotently every time a user is resolved. Ensures every user has the
system-default reports and their cron schedules, plus the hourly CheckIn
pulse. Safe to call on every inbound message — uses UNIQUE constraints and
existence checks to avoid duplicates.
"""

from __future__ import annotations

from . import reports, scheduled_tasks

# Default report templates -------------------------------------------------
_MORNING_BRIEF = {
    "name": "Morning Brief",
    "style_instructions": (
        "Concise and energizing. 5–8 short lines. Open with one calm framing "
        "sentence, then bullet today's P0/P1 action items, then today's scheduled "
        "events, then close with a single suggested focus for the next 90 minutes."
    ),
    "data_criteria": (
        "today's open P0 and P1 action_items; today's scheduled_tasks entries; "
        "any rolling_context_summary from the user_profile"
    ),
}

_EVENING_REVIEW = {
    "name": "Evening Review",
    "style_instructions": (
        "Warm and reflective. 4–7 lines. Surface today's completions, list any "
        "remaining open P0/P1 items, gently flag stale ones (>2 days), and end "
        "with a single suggested top task for tomorrow."
    ),
    "data_criteria": (
        "action_items completed today; open P0/P1; action_items open >2 days; "
        "rolling_context_summary"
    ),
}

# Default schedules --------------------------------------------------------
_DEFAULT_SCHEDULES = [
    # cron,           payload generator
    ("45 8 * * *",  lambda report_id: (f"agent:Reporter|report_id={report_id}", report_id)),
    ("0 23 * * *",  lambda report_id: (f"agent:Reporter|report_id={report_id}", report_id)),
]

# Hourly CheckIn pulse: 9AM–11PM every hour, no report_id.
_CHECKIN_CRON = "0 9-23 * * *"
_CHECKIN_PAYLOAD = "agent:CheckIn|hourly-pulse"


def seed_user_defaults(user_id: int) -> None:
    """Ensure user has default reports + their schedules + hourly CheckIn pulse."""
    morning = _ensure_report(user_id, _MORNING_BRIEF)
    evening = _ensure_report(user_id, _EVENING_REVIEW)

    # Pair each report to its cron in declaration order.
    pairs = [
        (_DEFAULT_SCHEDULES[0][0], morning["id"]),
        (_DEFAULT_SCHEDULES[1][0], evening["id"]),
    ]
    for cron_expr, report_id in pairs:
        payload = f"agent:Reporter|report_id={report_id}"
        if not scheduled_tasks.exists_for(user_id, cron_expr, payload):
            scheduled_tasks.create(
                user_id=user_id,
                cron_expression=cron_expr,
                prompt_payload=payload,
                report_id=report_id,
            )

    if not scheduled_tasks.exists_for(user_id, _CHECKIN_CRON, _CHECKIN_PAYLOAD):
        scheduled_tasks.create(
            user_id=user_id,
            cron_expression=_CHECKIN_CRON,
            prompt_payload=_CHECKIN_PAYLOAD,
        )


def _ensure_report(user_id: int, spec: dict) -> dict:
    existing = reports.get_by_name(user_id, spec["name"])
    if existing:
        return existing
    new_id = reports.create(
        user_id=user_id,
        name=spec["name"],
        style_instructions=spec["style_instructions"],
        data_criteria=spec["data_criteria"],
        is_default=True,
    )
    return reports.get(new_id)  # type: ignore[return-value]
