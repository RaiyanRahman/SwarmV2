"""reports table: per-user report templates (style + data criteria).

Reporter pulls a row at fire time and uses its style_instructions to phrase
the message and its data_criteria to decide what to fetch via tools.

System-seeded defaults (is_default=1):
  - Morning Brief
  - Evening Review

Users can ask Chronos to register additional reports at custom cadences.
"""

from __future__ import annotations

from typing import Optional

from . import connect

SCHEMA = """
CREATE TABLE IF NOT EXISTS reports (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id            INTEGER NOT NULL,
    name               TEXT NOT NULL,
    style_instructions TEXT NOT NULL,
    data_criteria      TEXT NOT NULL,
    is_default         BOOLEAN NOT NULL DEFAULT 0,
    created_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    UNIQUE (user_id, name)
)
"""


def create(
    user_id: int,
    name: str,
    style_instructions: str,
    data_criteria: str,
    is_default: bool = False,
) -> int:
    with connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO reports (user_id, name, style_instructions, data_criteria, is_default)
            VALUES (?, ?, ?, ?, ?)
            """,
            (user_id, name, style_instructions, data_criteria, 1 if is_default else 0),
        )
        return int(cur.lastrowid)


def get(report_id: int) -> Optional[dict]:
    with connect() as conn:
        row = conn.execute("SELECT * FROM reports WHERE id = ?", (report_id,)).fetchone()
        return dict(row) if row else None


def get_by_name(user_id: int, name: str) -> Optional[dict]:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM reports WHERE user_id = ? AND name = ?", (user_id, name)
        ).fetchone()
        return dict(row) if row else None


def list_for_user(user_id: int) -> list[dict]:
    with connect() as conn:
        return [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM reports WHERE user_id = ? ORDER BY is_default DESC, id ASC",
                (user_id,),
            ).fetchall()
        ]
