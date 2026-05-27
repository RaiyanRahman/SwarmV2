"""action_items table: P0–P4 prioritized tasks owned by TaskMaster."""

from __future__ import annotations

from typing import Literal, Optional

from . import connect

Priority = Literal["P0", "P1", "P2", "P3", "P4"]
VALID_PRIORITIES = ("P0", "P1", "P2", "P3", "P4")

SCHEMA = """
CREATE TABLE IF NOT EXISTS action_items (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL,
    title           TEXT NOT NULL,
    content_details TEXT,
    priority        TEXT NOT NULL CHECK (priority IN ('P0', 'P1', 'P2', 'P3', 'P4')),
    target_date     TIMESTAMP,
    is_completed    BOOLEAN NOT NULL DEFAULT 0,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
)
"""


def create(
    user_id: int,
    title: str,
    priority: Priority,
    content_details: Optional[str] = None,
    target_date: Optional[str] = None,
) -> int:
    if priority not in VALID_PRIORITIES:
        raise ValueError(f"priority must be one of {VALID_PRIORITIES}, got {priority!r}")
    if not title or not title.strip():
        raise ValueError("title is required")
    with connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO action_items (user_id, title, content_details, priority, target_date)
            VALUES (?, ?, ?, ?, ?)
            """,
            (user_id, title.strip(), content_details, priority, target_date),
        )
        return int(cur.lastrowid)


def get(item_id: int) -> Optional[dict]:
    with connect() as conn:
        row = conn.execute("SELECT * FROM action_items WHERE id = ?", (item_id,)).fetchone()
        return dict(row) if row else None


def list_for_user(
    user_id: int,
    priority: Optional[Priority] = None,
    include_completed: bool = False,
) -> list[dict]:
    sql = "SELECT * FROM action_items WHERE user_id = ?"
    params: list = [user_id]
    if not include_completed:
        sql += " AND is_completed = 0"
    if priority:
        sql += " AND priority = ?"
        params.append(priority)
    sql += " ORDER BY is_completed ASC, priority ASC, target_date ASC, id ASC"
    with connect() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


# Backwards-compatible alias for the now-removed list_open name.
def list_open(user_id: int, priority: Optional[Priority] = None) -> list[dict]:
    return list_for_user(user_id, priority=priority, include_completed=False)


def mark_completed(item_id: int, user_id: int) -> bool:
    """Mark item complete only if it belongs to user_id. Returns True if updated."""
    with connect() as conn:
        cur = conn.execute(
            "UPDATE action_items SET is_completed = 1 WHERE id = ? AND user_id = ?",
            (item_id, user_id),
        )
        return cur.rowcount > 0


def update(
    item_id: int,
    user_id: int,
    title: Optional[str] = None,
    content_details: Optional[str] = None,
    priority: Optional[Priority] = None,
    target_date: Optional[str] = None,
) -> bool:
    """Update only the provided fields. Ownership-scoped. Returns True if a row changed."""
    sets: list[str] = []
    params: list = []
    if title is not None:
        if not title.strip():
            raise ValueError("title cannot be empty")
        sets.append("title = ?")
        params.append(title.strip())
    if content_details is not None:
        sets.append("content_details = ?")
        params.append(content_details)
    if priority is not None:
        if priority not in VALID_PRIORITIES:
            raise ValueError(f"priority must be one of {VALID_PRIORITIES}, got {priority!r}")
        sets.append("priority = ?")
        params.append(priority)
    if target_date is not None:
        sets.append("target_date = ?")
        params.append(target_date)
    if not sets:
        return False
    params.extend([item_id, user_id])
    with connect() as conn:
        cur = conn.execute(
            f"UPDATE action_items SET {', '.join(sets)} WHERE id = ? AND user_id = ?",
            params,
        )
        return cur.rowcount > 0


def list_due_by(user_id: int, before_iso: str, only_open: bool = True) -> list[dict]:
    """Items with target_date <= before_iso. Used by Reporter for today's brief."""
    sql = "SELECT * FROM action_items WHERE user_id = ? AND target_date IS NOT NULL AND target_date <= ?"
    params: list = [user_id, before_iso]
    if only_open:
        sql += " AND is_completed = 0"
    sql += " ORDER BY priority ASC, target_date ASC, id ASC"
    with connect() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def list_completed_since(user_id: int, since_iso: str) -> list[dict]:
    """Items marked completed at or after since_iso. Used by Reporter's evening review."""
    # SQLite stores created_at; we approximate 'completed at' as items completed
    # whose row was last touched after since_iso. For a precise completed_at we
    # would add a column in a later migration.
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM action_items
            WHERE user_id = ? AND is_completed = 1 AND created_at >= ?
            ORDER BY priority ASC, id ASC
            """,
            (user_id, since_iso),
        ).fetchall()
        return [dict(r) for r in rows]


def list_stale(user_id: int, priorities: tuple[str, ...] = ("P0", "P1"), days: int = 2) -> list[dict]:
    """Items in given priorities older than N days and still open. Used by Self-Reflect."""
    with connect() as conn:
        rows = conn.execute(
            f"""
            SELECT * FROM action_items
            WHERE user_id = ?
              AND is_completed = 0
              AND priority IN ({",".join("?" * len(priorities))})
              AND created_at <= datetime('now', ?)
            ORDER BY created_at ASC
            """,
            (user_id, *priorities, f"-{int(days)} days"),
        ).fetchall()
        return [dict(r) for r in rows]
