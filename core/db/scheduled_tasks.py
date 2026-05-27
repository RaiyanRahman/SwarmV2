"""scheduled_tasks table: cron-driven background prompts owned by Scheduler."""

from __future__ import annotations

from typing import Optional

from . import connect

SCHEMA = """
CREATE TABLE IF NOT EXISTS scheduled_tasks (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL,
    cron_expression TEXT NOT NULL,
    prompt_payload  TEXT NOT NULL,
    is_active       BOOLEAN NOT NULL DEFAULT 1,
    next_run_at     TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
)
"""


def create(
    user_id: int,
    cron_expression: str,
    prompt_payload: str,
    report_id: Optional[int] = None,
) -> int:
    with connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO scheduled_tasks (user_id, cron_expression, prompt_payload, report_id)
            VALUES (?, ?, ?, ?)
            """,
            (user_id, cron_expression, prompt_payload, report_id),
        )
        return int(cur.lastrowid)


def exists_for(user_id: int, cron_expression: str, prompt_payload: str) -> bool:
    """Used by the bootstrap seeder to stay idempotent."""
    with connect() as conn:
        row = conn.execute(
            """
            SELECT 1 FROM scheduled_tasks
            WHERE user_id = ? AND cron_expression = ? AND prompt_payload = ?
            LIMIT 1
            """,
            (user_id, cron_expression, prompt_payload),
        ).fetchone()
        return row is not None


def list_active() -> list[dict]:
    with connect() as conn:
        return [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM scheduled_tasks WHERE is_active = 1"
            ).fetchall()
        ]


def set_active(task_id: int, active: bool) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE scheduled_tasks SET is_active = ? WHERE id = ?",
            (1 if active else 0, task_id),
        )


def set_next_run(task_id: int, next_run_at: Optional[str]) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE scheduled_tasks SET next_run_at = ? WHERE id = ?",
            (next_run_at, task_id),
        )
