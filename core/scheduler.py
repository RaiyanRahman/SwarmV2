"""Scheduler — APScheduler-backed cron loop for proactive agent runs.

Reads scheduled_tasks at boot, registers a cron job per row, and on fire
hands the prompt_payload to a callback supplied by the orchestrator layer.
Chronos is the agent that *registers* new entries; Scheduler executes them.
"""

from __future__ import annotations

import logging
from typing import Awaitable, Callable, Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from .db import scheduled_tasks

log = logging.getLogger(__name__)

# Callback signature: (user_id, prompt_payload) -> awaitable
FireCallback = Callable[[int, str], Awaitable[None]]


class Scheduler:
    """Owns the APScheduler instance and the live job registry."""

    def __init__(self, on_fire: FireCallback) -> None:
        self._on_fire = on_fire
        self._scheduler = AsyncIOScheduler()
        # APScheduler job ids must be strings; we use f"task-{db_id}"
        self._loaded: set[str] = set()

    # lifecycle ---------------------------------------------------------
    def start(self) -> None:
        if not self._scheduler.running:
            self._scheduler.start()
        self.reload_from_db()
        log.info("Scheduler started with %d active tasks", len(self._loaded))

    def shutdown(self) -> None:
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)

    # job management ----------------------------------------------------
    def reload_from_db(self) -> None:
        """Sync APScheduler with the scheduled_tasks table.

        Idempotent: existing jobs that match the DB are left in place;
        removed rows have their jobs cancelled; new rows are added.
        """
        rows = scheduled_tasks.list_active()
        seen: set[str] = set()
        for row in rows:
            job_id = self._job_id(row["id"])
            seen.add(job_id)
            if job_id in self._loaded:
                continue
            self._add_job(row)
        # Cancel jobs that disappeared
        for stale in self._loaded - seen:
            try:
                self._scheduler.remove_job(stale)
            except Exception:
                pass
        self._loaded = seen

    def _add_job(self, row: dict) -> None:
        job_id = self._job_id(row["id"])
        try:
            trigger = CronTrigger.from_crontab(row["cron_expression"])
        except Exception:
            log.exception("Invalid cron %r for task id=%s; skipping", row["cron_expression"], row["id"])
            return
        self._scheduler.add_job(
            self._fire,
            trigger=trigger,
            id=job_id,
            replace_existing=True,
            kwargs={"task_id": row["id"], "user_id": row["user_id"], "payload": row["prompt_payload"]},
        )
        log.info("Scheduled task id=%s cron=%r", row["id"], row["cron_expression"])

    async def _fire(self, task_id: int, user_id: int, payload: str) -> None:
        log.info("Firing scheduled task id=%s for user=%s", task_id, user_id)
        try:
            await self._on_fire(user_id, payload)
        except Exception:
            log.exception("Scheduled task id=%s failed", task_id)

    @staticmethod
    def _job_id(db_id: int) -> str:
        return f"task-{db_id}"


# Module-level registration so tools (e.g. register_schedule) can reach the
# live Scheduler without taking it as an argument.
_active: Optional["Scheduler"] = None


def set_active_scheduler(scheduler: "Scheduler") -> None:
    global _active
    _active = scheduler


def active_scheduler() -> Optional["Scheduler"]:
    return _active
