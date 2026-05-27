"""Build ADK agents from JSON, with watchdog-driven hot-reload.

Directory layout:
    agents/<AgentName>/agent.json       — agent definition (metadata + tools)
    agents/<AgentName>/instruction.txt  — system prompt (free-form text)
    tools/<tool_name>/tool.json         — tool schema
    tools/<tool_name>/code.py           — Python entrypoint exposing `run(**kwargs)`

agent.json schema:
{
  "name": "Orchestrator",
  "model": "ollama_chat/gemma4:e4b",        // optional override of gateway default
  "description": "...",
  "tools": ["create_action_item", ...],     // FunctionTools from tools/<name>/
  "agent_tools": ["TaskMaster", "Chronos"], // sub-agents exposed as AgentTool
  "output_key": "orchestrator_output"        // optional
}

`agent_tools` is intentionally distinct from ADK's built-in `sub_agents`
field. SwarmV2 forbids A2A: sub-agents do not delegate to one another. The
Orchestrator (and only the Orchestrator) calls them sequentially via
AgentTool wrappers, which makes every cross-agent transition an explicit
function_call event the parent LLM can observe.

The instruction lives in instruction.txt next to agent.json so it can be
edited freely (multi-line, no JSON escaping). Watchdog watches both files
and reloads the agent (and any composite that names it) when either changes.
"""

from __future__ import annotations

import importlib.util
import json
import logging
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from .llm_gateway import LLMGateway

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[1]
AGENTS_DIR = ROOT / "agents"
TOOLS_DIR = ROOT / "tools"

_DEBOUNCE_S = 0.3  # editors often emit multiple events per save


# ---------------------------------------------------------------------------
# Tool loading
# ---------------------------------------------------------------------------

def _load_tool_callable(tool_name: str) -> Callable[..., Any]:
    """Import tools/<tool_name>/code.py and return its `run` function."""
    code_path = TOOLS_DIR / tool_name / "code.py"
    if not code_path.exists():
        raise FileNotFoundError(f"tool entrypoint missing: {code_path}")
    spec = importlib.util.spec_from_file_location(f"tools.{tool_name}.code", code_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"could not load spec for {code_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not hasattr(module, "run"):
        raise AttributeError(f"tools/{tool_name}/code.py must define a `run` function")
    fn = module.run
    fn.__name__ = tool_name  # surfaced to the LLM as the tool name
    return fn


def _build_function_tool(tool_name: str):
    """Wrap the tool's `run` in an ADK FunctionTool."""
    from google.adk.tools import FunctionTool  # type: ignore

    fn = _load_tool_callable(tool_name)
    return FunctionTool(func=fn)


def _build_agent_tool(agent_name: str, reg: "AgentRegistry"):
    """Wrap an existing agent in an ADK AgentTool so a parent agent can call it."""
    from google.adk.tools.agent_tool import AgentTool  # type: ignore

    sub_agent = reg.get(agent_name)  # raises KeyError if not yet loaded
    return AgentTool(agent=sub_agent)


# ---------------------------------------------------------------------------
# Agent loading
# ---------------------------------------------------------------------------

def _read_instruction(agent_dir: Path) -> str:
    """Load the system prompt from agent_dir/instruction.txt."""
    path = agent_dir / "instruction.txt"
    if not path.exists():
        log.warning("No instruction.txt for agent dir %s", agent_dir)
        return ""
    return path.read_text(encoding="utf-8").strip()


def _build_agent(config_path: Path, reg: "AgentRegistry"):
    """Build an ADK LlmAgent from agent.json.

    The registry is passed in so composite agents (those declaring
    agent_tools) can resolve their sub-agent references. Leaf agents
    don't touch it.
    """
    from google.adk.agents import LlmAgent  # type: ignore

    data = json.loads(config_path.read_text())
    agent_dir = config_path.parent
    name = data.get("name") or agent_dir.name

    tools: list[Any] = []
    for tool_name in data.get("tools", []):
        try:
            tools.append(_build_function_tool(tool_name))
        except Exception:
            log.exception("Failed to load tool %r for agent %s; skipping", tool_name, name)

    for sub_name in data.get("agent_tools", []):
        try:
            tools.append(_build_agent_tool(sub_name, reg))
        except KeyError:
            log.warning(
                "Sub-agent %r referenced by %s not yet loaded; skipping for this pass",
                sub_name,
                name,
            )
        except Exception:
            log.exception("Failed to wrap sub-agent %r for %s; skipping", sub_name, name)

    gateway = LLMGateway.instance()
    model = gateway.build_model(data.get("model"))

    kwargs = dict(
        name=name,
        model=model,
        description=data.get("description", ""),
        instruction=_read_instruction(agent_dir),
        tools=tools,
    )
    if "output_key" in data:
        kwargs["output_key"] = data["output_key"]

    return LlmAgent(**kwargs)


def _read_agent_tools(config_path: Path) -> list[str]:
    """Read just the agent_tools list from agent.json without building anything."""
    try:
        data = json.loads(config_path.read_text())
        return list(data.get("agent_tools", []))
    except Exception:
        log.exception("Failed to parse %s for agent_tools listing", config_path)
        return []


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

class AgentRegistry:
    """In-memory map of agent name -> ADK agent instance.

    Thread-safe so the watchdog observer thread can swap entries while the
    asyncio loop reads them.
    """

    def __init__(self) -> None:
        self._agents: dict[str, Any] = {}
        self._lock = threading.RLock()

    def get(self, name: str):
        with self._lock:
            agent = self._agents.get(name)
        if agent is None:
            raise KeyError(f"agent {name!r} not loaded")
        return agent

    def names(self) -> list[str]:
        with self._lock:
            return list(self._agents.keys())

    def set(self, name: str, agent: Any) -> None:
        with self._lock:
            self._agents[name] = agent

    def remove(self, name: str) -> None:
        with self._lock:
            self._agents.pop(name, None)

    # --- bulk loading ----------------------------------------------------

    def load_all(self) -> None:
        """Two-pass load so composite agents can resolve their sub-agents.

        Pass 1: every agent that has NO agent_tools (leaves).
        Pass 2: every agent that has agent_tools (composites). By now their
        referenced sub-agents exist in the registry.

        Dependent rebuilds are suppressed during bulk load — composites get
        their explicit build in pass 2.
        """
        if not AGENTS_DIR.exists():
            log.warning("agents directory missing: %s", AGENTS_DIR)
            return

        configs = list(AGENTS_DIR.glob("*/agent.json"))
        leaves = [p for p in configs if not _read_agent_tools(p)]
        composites = [p for p in configs if _read_agent_tools(p)]

        for cp in leaves:
            self._reload_from_path(cp, rebuild_dependents=False)
        for cp in composites:
            self._reload_from_path(cp, rebuild_dependents=False)

    # --- single reload ---------------------------------------------------

    def _reload_from_path(self, config_path: Path, rebuild_dependents: bool = True) -> None:
        try:
            agent = _build_agent(config_path, self)
            self.set(agent.name, agent)
            log.info("Loaded agent %s from %s", agent.name, config_path)
        except Exception:
            log.exception("Failed to build agent from %s", config_path)
            return

        # If a leaf agent changed during a hot reload, every composite that
        # names it must be rebuilt so its AgentTool wrapper points at the
        # new instance. We skip this during bulk load (load_all does its
        # own explicit second pass).
        if rebuild_dependents:
            self._rebuild_dependents(agent.name)

    def _rebuild_dependents(self, changed_agent_name: str) -> None:
        """Re-emit any composite agent that lists `changed_agent_name` in its agent_tools."""
        for cp in AGENTS_DIR.glob("*/agent.json"):
            sub_names = _read_agent_tools(cp)
            if not sub_names or changed_agent_name not in sub_names:
                continue
            # Skip self-trigger if changed_agent_name IS the composite.
            try:
                this_name = json.loads(cp.read_text()).get("name", cp.parent.name)
            except Exception:
                this_name = cp.parent.name
            if this_name == changed_agent_name:
                continue
            try:
                composite = _build_agent(cp, self)
                self.set(composite.name, composite)
                log.info(
                    "Rebuilt composite %s after leaf %s changed",
                    composite.name,
                    changed_agent_name,
                )
            except Exception:
                log.exception("Failed to rebuild composite %s after leaf change", cp)


# ---------------------------------------------------------------------------
# Watchdog hot-reload
# ---------------------------------------------------------------------------

class _ConfigChangeHandler(FileSystemEventHandler):
    def __init__(self, registry: AgentRegistry) -> None:
        super().__init__()
        self._registry = registry
        self._last_fire: dict[str, float] = {}

    def _maybe_reload(self, path_str: str) -> None:
        path = Path(path_str)
        if path.name == "agent.json":
            config_path = path
        elif path.name == "instruction.txt":
            config_path = path.parent / "agent.json"
            if not config_path.exists():
                return
        else:
            return
        key = str(config_path)
        now = time.monotonic()
        prev = self._last_fire.get(key, 0.0)
        if now - prev < _DEBOUNCE_S:
            return
        self._last_fire[key] = now
        log.info("Detected change in %s, reloading agent", path)
        self._registry._reload_from_path(config_path)

    def on_modified(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._maybe_reload(event.src_path)

    def on_created(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._maybe_reload(event.src_path)

    def on_moved(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._maybe_reload(event.dest_path)


def start_hot_reload(registry: AgentRegistry) -> Observer:
    """Begin watching agents/ for changes."""
    handler = _ConfigChangeHandler(registry)
    observer = Observer()
    observer.schedule(handler, str(AGENTS_DIR), recursive=True)
    observer.daemon = True
    observer.start()
    log.info("Hot-reload watcher started on %s", AGENTS_DIR)
    return observer


# Module-level singleton ----------------------------------------------------
_registry: Optional[AgentRegistry] = None


def registry() -> AgentRegistry:
    global _registry
    if _registry is None:
        _registry = AgentRegistry()
    return _registry
