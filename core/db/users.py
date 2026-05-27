"""users table: maps Telegram IDs to internal user IDs."""

from __future__ import annotations

from typing import Optional

from . import connect

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_id  TEXT UNIQUE NOT NULL,
    user_handle  TEXT,
    created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
"""


def get_or_create(telegram_id: str, user_handle: Optional[str] = None) -> int:
    """Return internal user id for a Telegram id, creating the row if new."""
    telegram_id = str(telegram_id)
    with connect() as conn:
        row = conn.execute(
            "SELECT id FROM users WHERE telegram_id = ?", (telegram_id,)
        ).fetchone()
        if row is not None:
            return int(row["id"])
        cur = conn.execute(
            "INSERT INTO users (telegram_id, user_handle) VALUES (?, ?)",
            (telegram_id, user_handle),
        )
        return int(cur.lastrowid)


def get_by_id(user_id: int) -> Optional[dict]:
    with connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return dict(row) if row else None


def get_telegram_id(user_id: int) -> Optional[str]:
    with connect() as conn:
        row = conn.execute(
            "SELECT telegram_id FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        return row["telegram_id"] if row else None


def get_adk_session_id(user_id: int) -> Optional[str]:
    """Return the user's current persistent ADK session id, or None."""
    with connect() as conn:
        row = conn.execute(
            "SELECT adk_session_id FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        return row["adk_session_id"] if row else None


def set_adk_session_id(user_id: int, session_id: Optional[str]) -> None:
    """Persist (or clear) the user's main ADK conversation session id."""
    with connect() as conn:
        conn.execute(
            "UPDATE users SET adk_session_id = ? WHERE id = ?",
            (session_id, user_id),
        )
