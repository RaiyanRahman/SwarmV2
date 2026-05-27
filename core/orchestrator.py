"""Orchestrator — sequential routing between Scribe, Orchestrator-LLM, and overrides.

Flow for an inbound user message:
  1. StateManager check: is an agent override active (e.g. CHECK_IN_PENDING)?
     If yes, route straight to that agent on an ephemeral session.
  2. Otherwise: run the Orchestrator agent on the user's *persistent*
     ADK session (created on first contact, reused thereafter).
  3. Every LLM call goes through LLMGateway.serialized() so inference is
     strictly serialized.

Persistent vs ephemeral sessions
--------------------------------
Each user gets ONE persistent ADK session for their main conversation
thread (tracked by users.adk_session_id). Override agents like CheckIn
fire on transient prompts and use ephemeral sessions that are discarded
after the run — those bursts shouldn't pollute the user's rolling chat.

Scheduled proactive runs also use ephemeral sessions for the same reason.
"""

from __future__ import annotations

import logging
from typing import Optional

from .agent_factory import registry
from .db import DB_PATH, bootstrap, conversations, users
from .llm_gateway import LLMGateway
from .state_manager import StateManager

log = logging.getLogger(__name__)

APP_NAME = "SwarmV2"


class Orchestrator:
    def __init__(self) -> None:
        # Inference serialization happens at the TURN scope here. One full
        # inbound (or scheduled) run holds the gateway lock end-to-end,
        # so we get strict serialization across users/triggers without the
        # deadlock that lock-at-model exhibits under AgentTool composition.
        self._gateway = LLMGateway.instance()
        self._session_service = None  # built lazily so ADK import stays optional in tests

    # public --------------------------------------------------------------
    async def handle_inbound(
        self,
        text: str,
        telegram_id: str,
        user_data: dict,
    ) -> Optional[str]:
        """Top-level entrypoint called by CommunicationBridge."""
        user_id = users.get_or_create(telegram_id)
        bootstrap.seed_user_defaults(user_id)
        state = StateManager(user_data)
        state.set("INTERNAL_USER_ID", user_id)

        conversations.append(user_id, "user", text)

        async with self._gateway.lock:
            override = state.active_override()
            if override:
                # Override agents (CheckIn) send their own messages via the
                # send_telegram_message tool. Their returned text is internal
                # status and not surfaced to the user.
                log.info("Routing to override agent %s for user=%s", override, user_id)
                await self._run_agent(override, text, user_id, persistent=False)
                reply = None
            else:
                orch_output = await self._run_agent(
                    "Orchestrator", text, user_id, persistent=True
                )
                # Pass Orchestrator's structured summary through Scribe to get a
                # MarkdownV2 user-facing message. Scribe runs ephemeral — no
                # need for its rewriting history to persist.
                if orch_output and orch_output.strip():
                    reply = await self._run_agent(
                        "Scribe", orch_output, user_id, persistent=False
                    )
                else:
                    log.warning("Orchestrator returned empty output for user=%s", user_id)
                    reply = None

        if reply:
            conversations.append(user_id, "assistant", reply)
        return reply

    async def handle_scheduled(self, user_id: int, prompt_payload: str) -> None:
        """Entrypoint for Scheduler-fired proactive runs (no inbound user text)."""
        log.info("Handling scheduled prompt for user=%s", user_id)
        agent_name, body = self._parse_scheduled_payload(prompt_payload)
        async with self._gateway.lock:
            await self._run_agent(agent_name, body, user_id, persistent=False)

    # internals -----------------------------------------------------------
    @staticmethod
    def _parse_scheduled_payload(payload: str) -> tuple[str, str]:
        if payload.startswith("agent:"):
            head, _, body = payload.partition("|")
            return head.removeprefix("agent:").strip(), body.strip()
        return "Orchestrator", payload

    def _get_session_service(self):
        """Lazy-init the DatabaseSessionService pointed at our SQLite file."""
        if self._session_service is None:
            from google.adk.sessions import DatabaseSessionService  # type: ignore

            # ADK's DatabaseSessionService uses async SQLAlchemy, so the dialect
            # must be sqlite+aiosqlite (the plain sqlite:// driver is sync-only).
            self._session_service = DatabaseSessionService(
                db_url=f"sqlite+aiosqlite:///{DB_PATH}"
            )
        return self._session_service

    async def _resolve_session(self, user_id: int, persistent: bool):
        """Return an ADK session, creating it on first contact if persistent.

        For ephemeral runs (override agents, scheduled fires) we always
        create a throwaway session and never persist its id on users.
        """
        from google.adk.sessions import DatabaseSessionService  # noqa: F401  (ensure import path)

        svc = self._get_session_service()
        uid_str = str(user_id)

        if not persistent:
            return await svc.create_session(
                app_name=APP_NAME, user_id=uid_str, state={"user_id": user_id}
            )

        existing_id = users.get_adk_session_id(user_id)
        if existing_id:
            session = await svc.get_session(
                app_name=APP_NAME, user_id=uid_str, session_id=existing_id
            )
            if session is not None:
                # State could have been wiped in some edge cases; ensure user_id present.
                if session.state.get("user_id") != user_id:
                    session.state["user_id"] = user_id
                return session
            log.warning(
                "Stored adk_session_id=%s for user=%s not found; creating fresh",
                existing_id,
                user_id,
            )

        session = await svc.create_session(
            app_name=APP_NAME, user_id=uid_str, state={"user_id": user_id}
        )
        users.set_adk_session_id(user_id, session.id)
        return session

    async def _run_agent(
        self,
        agent_name: str,
        text: str,
        user_id: int,
        *,
        persistent: bool,
    ) -> Optional[str]:
        try:
            agent = registry().get(agent_name)
        except KeyError:
            log.error("Agent %s not loaded", agent_name)
            return f"(internal error: agent {agent_name!r} unavailable)"

        from google.adk.runners import Runner  # type: ignore
        from google.genai import types  # type: ignore

        session = await self._resolve_session(user_id, persistent=persistent)
        runner = Runner(
            agent=agent,
            app_name=APP_NAME,
            session_service=self._get_session_service(),
        )
        content = types.Content(role="user", parts=[types.Part(text=text)])

        # Inference serialization happens at the model layer (LockedLiteLlm),
        # NOT around this run — wrapping the entire run would deadlock as
        # soon as an AgentTool triggers a sub-agent inference mid-stream.
        final_text: Optional[str] = None
        async for event in runner.run_async(
            user_id=str(user_id),
            session_id=session.id,
            new_message=content,
        ):
            if event.is_final_response() and event.content and event.content.parts:
                final_text = "".join(p.text or "" for p in event.content.parts)
        return final_text
