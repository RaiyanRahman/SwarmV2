"""Orchestrator — sequential routing between Scribe, Orchestrator-LLM, and overrides.

Flow for an inbound user message:
  1. StateManager check: is an agent override active (e.g. CHECK_IN_PENDING)?
     If yes, route straight to that agent. The Orchestrator-LLM is skipped.
  2. Otherwise: run Scribe (sanitize + intent extraction) → Orchestrator-LLM
     (plans + invokes sub-agent tools sequentially) → Scribe (format reply).
  3. Every LLM call goes through LLMGateway.serialized() so inference is
     strictly serialized.

This module deliberately keeps no per-user state — that lives in StateManager
(short-term) and the database (long-term).
"""

from __future__ import annotations

import logging
from typing import Optional

from .agent_factory import registry
from .db import conversations, users
from .llm_gateway import LLMGateway
from .state_manager import StateManager

log = logging.getLogger(__name__)


class Orchestrator:
    def __init__(self) -> None:
        self._gateway = LLMGateway.instance()

    async def handle_inbound(
        self,
        text: str,
        telegram_id: str,
        user_data: dict,
    ) -> Optional[str]:
        """Top-level entrypoint called by CommunicationBridge."""
        user_id = users.get_or_create(telegram_id)
        state = StateManager(user_data)
        state.set("INTERNAL_USER_ID", user_id)

        conversations.append(user_id, "user", text)

        override = state.active_override()
        if override:
            log.info("Routing to override agent %s for user=%s", override, user_id)
            reply = await self._run_agent(override, text, user_id)
        else:
            reply = await self._run_pipeline(text, user_id)

        if reply:
            conversations.append(user_id, "assistant", reply)
        return reply

    async def handle_scheduled(self, user_id: int, prompt_payload: str) -> None:
        """Entrypoint for Scheduler-fired proactive runs (no inbound user text).

        Typical use: a Reporter morning brief or a CheckIn nudge. The result is
        delivered via the CommunicationBridge by the sub-agent's own tool calls,
        so this method returns None.
        """
        log.info("Handling scheduled prompt for user=%s", user_id)
        # By convention scheduled payloads are routed to Orchestrator unless the
        # payload specifies an explicit agent prefix like "agent:CheckIn|...".
        agent_name, body = self._parse_scheduled_payload(prompt_payload)
        await self._run_agent(agent_name, body, user_id)

    # --- internals ---------------------------------------------------------
    @staticmethod
    def _parse_scheduled_payload(payload: str) -> tuple[str, str]:
        if payload.startswith("agent:"):
            head, _, body = payload.partition("|")
            return head.removeprefix("agent:").strip(), body.strip()
        return "Orchestrator", payload

    async def _run_pipeline(self, text: str, user_id: int) -> Optional[str]:
        # Scribe (inbound) → Orchestrator-LLM → Scribe (outbound) is the canonical
        # path. Implementing the full ADK Runner wiring is Phase 2 work; for now
        # we execute the Orchestrator agent directly and let Scribe format inside
        # its tool calls. The structure stays sequential and lock-serialized.
        return await self._run_agent("Orchestrator", text, user_id)

    async def _run_agent(self, agent_name: str, text: str, user_id: int) -> Optional[str]:
        try:
            agent = registry().get(agent_name)
        except KeyError:
            log.error("Agent %s not loaded", agent_name)
            return f"(internal error: agent {agent_name!r} unavailable)"

        from google.adk.runners import InMemoryRunner  # type: ignore
        from google.genai import types  # type: ignore

        runner = InMemoryRunner(agent=agent, app_name="SwarmV2")
        session = await runner.session_service.create_session(
            app_name="SwarmV2", user_id=str(user_id)
        )
        content = types.Content(role="user", parts=[types.Part(text=text)])

        async def _run() -> Optional[str]:
            final_text: Optional[str] = None
            async for event in runner.run_async(
                user_id=str(user_id),
                session_id=session.id,
                new_message=content,
            ):
                if event.is_final_response() and event.content and event.content.parts:
                    final_text = "".join(p.text or "" for p in event.content.parts)
            return final_text

        return await self._gateway.serialized(_run())
