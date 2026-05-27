"""SwarmV2 entrypoint.

Boots, in order:
  1. SQLite schema (core.db.init_all)
  2. LLMGateway singleton (Ollama config)
  3. AgentRegistry — loads every agents/<Name>/agent.json
  4. Watchdog observer — hot-reload on JSON saves
  5. CommunicationBridge (Telegram)
  6. Orchestrator wired to bridge
  7. Scheduler wired to orchestrator.handle_scheduled
  8. PTB polling loop (blocks)

All env vars are read at startup and never re-read at runtime.
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Optional

from dotenv import load_dotenv

from core import db
from core.agent_factory import registry, start_hot_reload
from core.communication_bridge import CommunicationBridge, set_active_bridge
from core.llm_gateway import LLMConfig, LLMGateway
from core.orchestrator import Orchestrator
from core.scheduler import Scheduler, set_active_scheduler

# Load project-local .env before any os.environ reads in main().
load_dotenv()

log = logging.getLogger(__name__)


def _setup_logging() -> None:
    logging.basicConfig(
        level=os.environ.get("SWARM_LOG_LEVEL", "INFO"),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


def _read_env() -> tuple[str, Optional[set[str]]]:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        print("FATAL: TELEGRAM_BOT_TOKEN env var is required", file=sys.stderr)
        sys.exit(1)

    raw_allow = os.environ.get("TELEGRAM_ALLOWED_IDS", "").strip()
    allowed: Optional[set[str]] = None
    if raw_allow:
        allowed = {p.strip() for p in raw_allow.split(",") if p.strip()}
    return token, allowed


def main() -> None:
    _setup_logging()
    token, allowed = _read_env()

    # 1. DB schema
    db.init_all()
    log.info("Database initialized at %s", db.DB_PATH)

    # 2. LLM gateway
    LLMGateway.configure(LLMConfig(
        model=os.environ.get("SWARM_MODEL", "ollama_chat/gemma4:e4b"),
        api_base=os.environ.get("OLLAMA_API_BASE", "http://localhost:11434"),
    ))

    # 3. Agent registry
    reg = registry()
    reg.load_all()
    log.info("Loaded agents: %s", reg.names())

    # 4. Hot reload
    observer = start_hot_reload(reg)

    # 5. Telegram bridge
    bridge = CommunicationBridge(token=token, allowed_telegram_ids=allowed)
    app = bridge.build()
    set_active_bridge(bridge)

    # 6. Orchestrator
    orchestrator = Orchestrator()
    bridge.set_inbound_handler(orchestrator.handle_inbound)

    # 7. Scheduler
    scheduler = Scheduler(on_fire=orchestrator.handle_scheduled)
    set_active_scheduler(scheduler)

    async def _post_init(_app):
        scheduler.start()

    async def _on_shutdown(_app):
        scheduler.shutdown()
        observer.stop()
        observer.join(timeout=2)

    app.post_init = _post_init
    app.post_shutdown = _on_shutdown

    # 8. Polling
    log.info("Starting Telegram polling loop")
    app.run_polling(allowed_updates=["message"])


if __name__ == "__main__":
    main()
