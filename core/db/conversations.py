"""conversations table: rolling message log; Archivist indexes when full."""

from __future__ import annotations

from typing import Literal

from . import connect

Role = Literal["user", "assistant", "system", "tool"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id           INTEGER NOT NULL,
    message_role      TEXT NOT NULL CHECK (message_role IN ('user', 'assistant', 'system', 'tool')),
    payload_text      TEXT NOT NULL,
    archivist_indexed BOOLEAN NOT NULL DEFAULT 0,
    created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
)
"""


def append(user_id: int, role: Role, payload_text: str) -> int:
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO conversations (user_id, message_role, payload_text) VALUES (?, ?, ?)",
            (user_id, role, payload_text),
        )
        return int(cur.lastrowid)


def active_window(user_id: int, limit: int = 50) -> list[dict]:
    """Return the most recent un-archived messages, oldest first."""
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM conversations
            WHERE user_id = ? AND archivist_indexed = 0
            ORDER BY id DESC LIMIT ?
            """,
            (user_id, limit),
        ).fetchall()
        return [dict(r) for r in reversed(rows)]


def active_count(user_id: int) -> int:
    with connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM conversations WHERE user_id = ? AND archivist_indexed = 0",
            (user_id,),
        ).fetchone()
        return int(row["n"])


def mark_archived(ids: list[int]) -> None:
    if not ids:
        return
    placeholders = ",".join("?" * len(ids))
    with connect() as conn:
        conn.execute(
            f"UPDATE conversations SET archivist_indexed = 1 WHERE id IN ({placeholders})",
            ids,
        )
