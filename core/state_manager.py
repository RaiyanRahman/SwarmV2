"""Session-state helpers backed by python-telegram-bot's context.user_data.

Architectural rule: short-term, deterministic pointers (CHECK_IN_PENDING,
last_action_item_id, etc.) live here so the LLM never has to derive them
from the database. The keys are typed constants to avoid string drift.
"""

from __future__ import annotations

from typing import Any, Optional

# Session keys ---------------------------------------------------------------
CHECK_IN_PENDING = "CHECK_IN_PENDING"            # bool
CHECK_IN_TASK_ID = "CHECK_IN_TASK_ID"            # int (action_items.id under follow-up)
LAST_ACTION_ITEM_ID = "LAST_ACTION_ITEM_ID"     # int
INTERNAL_USER_ID = "INTERNAL_USER_ID"            # int (users.id)
ACTIVE_AGENT_OVERRIDE = "ACTIVE_AGENT_OVERRIDE"  # str — bypass Orchestrator

ALL_KEYS = (
    CHECK_IN_PENDING,
    CHECK_IN_TASK_ID,
    LAST_ACTION_ITEM_ID,
    INTERNAL_USER_ID,
    ACTIVE_AGENT_OVERRIDE,
)


class StateManager:
    """Thin wrapper over a `context.user_data` dict.

    `user_data` is a MutableMapping provided per-chat by PTB. We accept any
    mapping so unit tests can pass a plain dict.
    """

    def __init__(self, user_data: dict[str, Any]):
        self._d = user_data

    # generic ---------------------------------------------------------------
    def get(self, key: str, default: Any = None) -> Any:
        return self._d.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self._d[key] = value

    def clear(self, key: str) -> None:
        self._d.pop(key, None)

    # check-in loop --------------------------------------------------------
    def start_check_in(self, action_item_id: int) -> None:
        self._d[CHECK_IN_PENDING] = True
        self._d[CHECK_IN_TASK_ID] = action_item_id

    def end_check_in(self) -> None:
        self._d.pop(CHECK_IN_PENDING, None)
        self._d.pop(CHECK_IN_TASK_ID, None)

    def is_check_in_pending(self) -> bool:
        return bool(self._d.get(CHECK_IN_PENDING))

    # agent override (e.g., CheckIn bypassing Orchestrator) --------------
    def override_agent(self, agent_name: Optional[str]) -> None:
        if agent_name is None:
            self._d.pop(ACTIVE_AGENT_OVERRIDE, None)
        else:
            self._d[ACTIVE_AGENT_OVERRIDE] = agent_name

    def active_override(self) -> Optional[str]:
        return self._d.get(ACTIVE_AGENT_OVERRIDE)
