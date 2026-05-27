"""SwarmV2 database package.

One SQLite file under data/swarm.db, one Python module per table.
Each table module exposes:
  - SCHEMA: the CREATE TABLE statement
  - data-access helpers (insert/get/update) scoped to that table

The package exposes connect() and init_all() so the app can bring the
database online at startup without reaching into individual modules.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Iterable

DB_PATH = Path(__file__).resolve().parents[2] / "data" / "swarm.db"


def connect() -> sqlite3.Connection:
    """Return a connection with FK + WAL enabled.

    SQLite connections are not safe to share across threads by default;
    open a fresh connection per caller. WAL mode lets the scheduler and
    the bot read concurrently while one writes.
    """
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


def init_all() -> None:
    """Create every table if missing. Idempotent.

    Also runs lightweight column migrations (ADD COLUMN IF NOT EXISTS-style)
    for changes added after the initial schema landed.
    """
    from . import (
        users,
        user_profile,
        conversations,
        action_items,
        scheduled_tasks,
        reports,
    )

    schemas: Iterable[str] = (
        users.SCHEMA,
        user_profile.SCHEMA,
        conversations.SCHEMA,
        action_items.SCHEMA,
        reports.SCHEMA,         # must precede scheduled_tasks (FK target)
        scheduled_tasks.SCHEMA,
    )
    with connect() as conn:
        for ddl in schemas:
            conn.execute(ddl)
        _migrate(conn)


def _migrate(conn) -> None:
    """Apply additive migrations. SQLite has no IF NOT EXISTS for columns."""
    st_cols = {r["name"] for r in conn.execute("PRAGMA table_info(scheduled_tasks)").fetchall()}
    if "report_id" not in st_cols:
        conn.execute(
            "ALTER TABLE scheduled_tasks ADD COLUMN report_id INTEGER REFERENCES reports(id) ON DELETE CASCADE"
        )
    u_cols = {r["name"] for r in conn.execute("PRAGMA table_info(users)").fetchall()}
    if "adk_session_id" not in u_cols:
        conn.execute("ALTER TABLE users ADD COLUMN adk_session_id TEXT")
