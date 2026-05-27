"""LLM gateway — single asyncio.Lock serializing every Ollama inference.

The 16GB unified-memory M4 cannot run two Gemma generations in parallel
without thrashing. Every code path that triggers an LLM call MUST go
through this gateway, either by:
  - constructing its model via build_model() so the lock-wrapped class is used, or
  - wrapping the run inside `async with gateway.lock:` at the orchestrator layer.

The orchestrator uses the lock at the run level so we never depend on
ADK internals to enforce serialization.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Optional

log = logging.getLogger(__name__)


@dataclass
class LLMConfig:
    model: str = "ollama_chat/gemma4:e4b"
    api_base: str = "http://localhost:11434"
    request_timeout_s: float = 120.0


class LLMGateway:
    """Process-wide singleton owning the inference lock."""

    _instance: Optional["LLMGateway"] = None

    def __init__(self, config: Optional[LLMConfig] = None) -> None:
        self.config = config or LLMConfig()
        # Plain asyncio.Lock — taken at TURN scope by the Orchestrator, NOT
        # per-inference. Lock-at-model deadlocks under AgentTool composition.
        self.lock = asyncio.Lock()
        self._inflight = 0

    @classmethod
    def instance(cls) -> "LLMGateway":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def configure(cls, config: LLMConfig) -> "LLMGateway":
        cls._instance = cls(config)
        return cls._instance

    def build_model(self, model_override: Optional[str] = None):
        """Return a plain LiteLlm pointed at our Ollama endpoint.

        We do NOT wrap the model with a per-inference lock. Lock-at-model
        deadlocks under ADK AgentTool composition because ADK runs the
        sub-agent in a separate asyncio task while the parent's
        generate_content_async generator is still suspended at its `yield`,
        so the lock can't be re-entered. Instead, the Orchestrator takes
        the gateway lock at the TURN scope (one full inbound or scheduled
        run at a time), which gives us cross-user/cross-trigger
        serialization without lock-across-yield issues. See
        Orchestrator.handle_inbound for the call site.
        """
        from google.adk.models.lite_llm import LiteLlm  # type: ignore

        return LiteLlm(
            model=model_override or self.config.model,
            api_base=self.config.api_base,
        )
