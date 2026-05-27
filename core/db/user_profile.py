"""user_profile table: traits, routines, and rolling context summary."""

from __future__ import annotations

import json
from typing import Optional

from . import connect

SCHEMA = """
CREATE TABLE IF NOT EXISTS user_profile (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id                 INTEGER NOT NULL UNIQUE,
    traits_json             TEXT,
    routines_json           TEXT,
    rolling_context_summary TEXT,
    last_updated_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
)
"""


def upsert(
    user_id: int,
    traits: Optional[dict] = None,
    routines: Optional[dict] = None,
    rolling_context_summary: Optional[str] = None,
) -> None:
    traits_json = json.dumps(traits) if traits is not None else None
    routines_json = json.dumps(routines) if routines is not None else None
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO user_profile (user_id, traits_json, routines_json, rolling_context_summary)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                traits_json             = COALESCE(excluded.traits_json, user_profile.traits_json),
                routines_json           = COALESCE(excluded.routines_json, user_profile.routines_json),
                rolling_context_summary = COALESCE(excluded.rolling_context_summary, user_profile.rolling_context_summary),
                last_updated_at         = CURRENT_TIMESTAMP
            """,
            (user_id, traits_json, routines_json, rolling_context_summary),
        )


def get(user_id: int) -> Optional[dict]:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM user_profile WHERE user_id = ?", (user_id,)
        ).fetchone()
        if row is None:
            return None
        out = dict(row)
        out["traits"] = json.loads(out["traits_json"]) if out["traits_json"] else {}
        out["routines"] = json.loads(out["routines_json"]) if out["routines_json"] else {}
        return out
