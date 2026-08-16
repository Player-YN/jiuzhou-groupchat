"""On-demand reflection — Stage 9 / P0-A.

Event-driven reflection: when a high-signal event happens, the system makes
**ONE** LLM call to generate a short reflection and stores it as an
``AgentMemoryEntry``.  **No scheduler, no daily cron, no
``asyncio.create_task`` loop.**  Pure event-driven — three trigger
conditions:

1.  **Long silence**: no group activity for >= 60 minutes.
2.  **Self over-mentioned**: a single NPC got @-ed 3+ times in 10 minutes.
3.  **Deep user input**: a user message > 200 chars.

When triggered, call ``stream_via_letta_with_retry`` for the target NPC
with a short reflection prompt, store the result as a new
``AgentMemoryEntry`` with ``source='group'``, ``speaker_key='system'``,
``role='agent'``, ``text=<result>``.  The next time any code reads recent
group events, this reflection will appear in the timeline (because
``load_recent_group_events`` filters only by ``source='group'`` and does
NOT filter by ``agent_key``).

The choice of ``source='group'`` over a hypothetical ``source='reflection'``
literal is intentional: ``AgentMemoryStore``'s SQLite CHECK constraint
enforces ``source IN ('group', 'dm')``.  We keep the discriminator on
``speaker_key='system'`` (per spec) — the spec writer's intent is
"appear in the recent-group-events timeline" which is exactly what
``source='group'`` does.

This module follows the project pattern (``npc_loop.py`` / ``xiuzhen_cron.py``)
of **no scheduled task** — every method is called explicitly by the caller
(WebSocket handler / admin endpoint / test).  The service object itself
holds NO background tasks; it is a pure function object over injected
dependencies.

Public surface:

- ``ReflectionEvent``                       — dataclass describing one fired reflection
- ``OnDemandReflectionService``             — the 3 event handlers + status_dict
- ``get_on_demand_reflection_service(...)`` — process-wide singleton accessor
- ``set_on_demand_reflection_service(...)`` — test injection helper
"""
from __future__ import annotations

import logging
import random
import threading
import time
from dataclasses import dataclass
from typing import Any, AsyncIterator, Callable, Optional

from langchain_core.messages import HumanMessage, SystemMessage

from app.graph import ROLES
from app.letta_bridge.agent_manager import ROLE_AGENT_KEYS
from app.scheduler.letta_retry import stream_via_letta_with_retry


logger = logging.getLogger(__name__)


# ============================================================================
# Constants — trigger thresholds (P0-A spec, no env override)
# ============================================================================
# A user message longer than this many characters triggers a deep_user_input
# reflection.  Spec: "> 200 chars".
_DEEP_USER_INPUT_MIN_CHARS: int = 200

# Group silence longer than this many minutes triggers a long_silence
# reflection.  Spec: ">= 60 minutes".
_LONG_SILENCE_MIN_MINUTES: float = 60.0

# A single NPC @-mentioned this many times within 10 minutes triggers a
# self_over_mentioned reflection.  Spec: "3+ times in 10 minutes".
_SELF_OVER_MENTIONED_MIN: int = 3


# ============================================================================
# Reflection prompt templates (P0-A spec, verbatim except for placeholders)
# ============================================================================
_PROMPT_DEEP_USER_INPUT_FMT: str = (
    "用户刚发了一段深度消息（{len} 字）。"
    "你是【{npc_name}】（{role_key}）。"
    "哪个角度你有想法？说 1 句即可，不超过 60 字。"
)
_PROMPT_LONG_SILENCE_FMT: str = (
    "群里 {silence_min} 分钟没人说话了。"
    "你是【{npc_name}】（{role_key}）。"
    "你想接一句吗？说 1 句即可，不超过 60 字。"
)
_PROMPT_SELF_OVER_MENTIONED_FMT: str = (
    "你今天被叫到 {count} 次了。"
    "要不要休息，还是继续？1 句即可。"
)

# Fallback session_id when the caller forgets to provide one.  The reflection
# still has to be persisted under SOME session_id (AgentMemoryStore schema
# requires it); using a sentinel keeps it isolated from real user sessions.
_FALLBACK_SESSION_ID: str = "reflection-default"


# ============================================================================
# Data class
# ============================================================================
@dataclass
class ReflectionEvent:
    """One fired reflection (success OR failure).

    The same object is returned to the caller regardless of LLM success so
    that callers can pattern-match on ``event_type`` and read ``error`` to
    decide whether to surface the failure in the UI / logs.

    Attributes:
        event_type:  "long_silence" | "self_over_mentioned" | "deep_user_input"
        session_id:  the (session_id, agent_key) memory slot we wrote to
        role_key:    target NPC key (one of ``ROLE_AGENT_KEYS``).  May be a
                     random pick (for ``long_silence`` / ``deep_user_input``
                     when caller doesn't pin a target).
        prompt:      the literal text we sent to the LLM (for debugging)
        result_text: concatenated LLM response (empty string on LLM failure)
        created_at:  ``time.time()`` at fire
        success:     True iff both LLM and storage succeeded
        error:       ``None`` on success; ``"llm: ..."`` / ``"storage: ..."``
                     with ``{ExceptionType}: {message}`` on failure
    """

    event_type: str
    session_id: str
    role_key: Optional[str]
    prompt: str
    result_text: str
    created_at: float
    success: bool
    error: Optional[str] = None


# ============================================================================
# Service
# ============================================================================
# Type alias for the injected LLM stream function.  Default uses
# ``app.scheduler.letta_retry.stream_via_letta_with_retry``.
LettaStreamFn = Callable[..., AsyncIterator[str]]


class OnDemandReflectionService:
    """Event-driven reflection service.

    Holds NO background tasks.  Each ``on_*`` method, when triggered, makes
    exactly ONE LLM call and (on success) ONE storage call.  All exceptions
    during LLM call or storage are caught and recorded in
    ``ReflectionEvent.error`` with ``success=False`` — the event is still
    returned so callers can observe the trigger.

    Dependencies are injected via the constructor; defaults use the
    process-wide singletons (``app.memory.get_agent_memory_store`` and
    ``app.scheduler.letta_retry.stream_via_letta_with_retry``).  Tests
    pass mocks; production code uses defaults.
    """

    def __init__(
        self,
        *,
        memory_store: Any = None,
        letta_stream_fn: Optional[LettaStreamFn] = None,
    ) -> None:
        # Late-resolve the memory_store default so tests can patch
        # ``app.memory.get_agent_memory_store`` before constructing the
        # service.  Same pattern as ``npc_loop._one_cycle``.
        if memory_store is None:
            from app.memory import get_agent_memory_store

            memory_store = get_agent_memory_store()
        self._memory_store = memory_store
        self._letta_stream_fn: LettaStreamFn = (
            letta_stream_fn if letta_stream_fn is not None else stream_via_letta_with_retry
        )
        # Counters (thread-safe increment via lock).
        self._lock = threading.Lock()
        self._total: int = 0
        self._by_type: dict[str, int] = {
            "long_silence": 0,
            "self_over_mentioned": 0,
            "deep_user_input": 0,
        }
        self._last_error: Optional[str] = None

    # ------------------------------------------------------------------
    # Trigger: deep user input (> 200 chars)
    # ------------------------------------------------------------------
    async def on_user_message(self, msg: dict) -> Optional[ReflectionEvent]:
        """Trigger if ``msg['text']`` length > 200.

        Picks a random NPC if ``msg`` does not carry a target role_key.
        Returns the event (even on LLM/storage failure) so callers know
        the trigger fired.  Returns ``None`` if the message is too short
        (no reflection needed).
        """
        if not isinstance(msg, dict):
            return None
        text = msg.get("text") or ""
        if not isinstance(text, str) or len(text) <= _DEEP_USER_INPUT_MIN_CHARS:
            return None
        # Pick target NPC.  Prefer explicit hint in msg; otherwise random.
        role_key = msg.get("target_role_key")
        if not role_key or role_key not in ROLES:
            role_key = random.choice(list(ROLE_AGENT_KEYS))
        prompt = _PROMPT_DEEP_USER_INPUT_FMT.format(
            len=len(text),
            npc_name=ROLES[role_key]["name"],
            role_key=role_key,
        )
        session_id = msg.get("session_id") or _FALLBACK_SESSION_ID
        return await self._run(
            event_type="deep_user_input",
            session_id=session_id,
            role_key=role_key,
            prompt=prompt,
        )

    # ------------------------------------------------------------------
    # Trigger: long silence (>= 60 min)
    # ------------------------------------------------------------------
    async def on_silence(
        self,
        session_id: str,
        silence_duration_min: float,
    ) -> Optional[ReflectionEvent]:
        """Trigger if ``silence_duration_min`` >= 60.

        For long_silence with no specific ``role_key`` target, picks a
        random NPC from ``ROLE_AGENT_KEYS`` (per P0-A spec).
        """
        if not isinstance(silence_duration_min, (int, float)):
            return None
        if silence_duration_min < _LONG_SILENCE_MIN_MINUTES:
            return None
        # Pick random NPC.  long_silence has no inherent target.
        role_key = random.choice(list(ROLE_AGENT_KEYS))
        # Format the silence minutes: keep integer clean ("60" not "60.0"),
        # fall through to float for non-integer values.
        silence_str = (
            str(int(silence_duration_min))
            if float(silence_duration_min).is_integer()
            else str(silence_duration_min)
        )
        prompt = _PROMPT_LONG_SILENCE_FMT.format(
            silence_min=silence_str,
            npc_name=ROLES[role_key]["name"],
            role_key=role_key,
        )
        sid = session_id or _FALLBACK_SESSION_ID
        return await self._run(
            event_type="long_silence",
            session_id=sid,
            role_key=role_key,
            prompt=prompt,
        )

    # ------------------------------------------------------------------
    # Trigger: self over-mentioned (>= 3 in 10 min)
    # ------------------------------------------------------------------
    async def on_self_over_mentioned(
        self,
        session_id: str,
        role_key: str,
        mention_count_10min: int,
    ) -> Optional[ReflectionEvent]:
        """Trigger if ``mention_count_10min`` >= 3.

        The target NPC is given explicitly (no random pick — we know which
        NPC was over-mentioned).
        """
        if not role_key or role_key not in ROLES:
            return None
        if not isinstance(mention_count_10min, int) or (
            mention_count_10min < _SELF_OVER_MENTIONED_MIN
        ):
            return None
        prompt = _PROMPT_SELF_OVER_MENTIONED_FMT.format(count=mention_count_10min)
        sid = session_id or _FALLBACK_SESSION_ID
        return await self._run(
            event_type="self_over_mentioned",
            session_id=sid,
            role_key=role_key,
            prompt=prompt,
        )

    # ------------------------------------------------------------------
    # status
    # ------------------------------------------------------------------
    def status_dict(self) -> dict:
        """JSON-friendly stats: ``total_reflections``, ``by_type`` dict, ``last_error``.

        Snapshot of internal counters under the lock.  Used by future
        ``/api/reflection/status`` admin endpoint.
        """
        with self._lock:
            return {
                "total_reflections": self._total,
                "by_type": dict(self._by_type),
                "last_error": self._last_error,
            }

    # ------------------------------------------------------------------
    # internal — one LLM call + one storage call, never raises
    # ------------------------------------------------------------------
    async def _run(
        self,
        *,
        event_type: str,
        session_id: str,
        role_key: str,
        prompt: str,
    ) -> ReflectionEvent:
        """Make 1 LLM call + 1 storage call.  Never raises.

        Returns a fully-populated ``ReflectionEvent`` regardless of
        success/failure so the caller always sees that the trigger fired.
        """
        created_at = time.time()
        result_text = ""
        success = True
        error: Optional[str] = None

        # 1) LLM call — concatenate the streamed chunks.
        full_text = ""
        system_msg = SystemMessage(content=ROLES[role_key]["system"])
        user_msg = HumanMessage(content=prompt)
        try:
            async for piece in self._letta_stream_fn(
                role_key=role_key,
                session_id=session_id,
                all_msgs=[system_msg, user_msg],
            ):
                full_text += piece
        except Exception as exc:  # noqa: BLE001 — must never escape per spec
            success = False
            error = f"llm: {type(exc).__name__}: {exc}"
            logger.warning(
                "[on_demand_reflection] %s LLM failed: %s", event_type, exc,
            )

        # 2) Storage — only attempt if LLM succeeded.  We don't store
        # partial garbage on LLM failure.  Storage failure here is also
        # recorded but the event is still returned.
        if success:
            try:
                self._memory_store.append_message(
                    session_id=session_id,
                    agent_key=role_key,
                    role="agent",
                    source="group",  # so it shows up in load_recent_group_events
                    speaker_key="system",  # P0-A spec discriminator
                    text=full_text or "(empty)",
                    agent_name=ROLES[role_key]["name"],
                    agent_emoji=ROLES[role_key]["emoji"],
                )
            except Exception as exc:  # noqa: BLE001
                success = False
                error = f"storage: {type(exc).__name__}: {exc}"
                logger.warning(
                    "[on_demand_reflection] %s storage failed: %s", event_type, exc,
                )
            else:
                result_text = full_text

        # Bookkeeping — always increment total + by_type, even on failure,
        # so callers can observe the trigger rate vs the success rate.
        with self._lock:
            self._total += 1
            self._by_type[event_type] = self._by_type.get(event_type, 0) + 1
            if error is not None:
                self._last_error = error

        return ReflectionEvent(
            event_type=event_type,
            session_id=session_id,
            role_key=role_key,
            prompt=prompt,
            result_text=result_text,
            created_at=created_at,
            success=success,
            error=error,
        )


# ============================================================================
# Process-wide singleton (mirrors AgentMemoryStore / NpcLoopPool pattern)
# ============================================================================
_service: Optional[OnDemandReflectionService] = None
_service_lock = threading.Lock()


def get_on_demand_reflection_service() -> OnDemandReflectionService:
    """Return the process-wide ``OnDemandReflectionService`` (lazy init)."""
    global _service
    with _service_lock:
        if _service is None:
            _service = OnDemandReflectionService()
        return _service


def set_on_demand_reflection_service(
    svc: Optional[OnDemandReflectionService],
) -> Optional[OnDemandReflectionService]:
    """Replace (or clear) the singleton.  Returns the previous instance.

    Tests pass a service with stubbed ``memory_store`` + ``letta_stream_fn``
    to get isolation.  Production code does NOT call this.
    """
    global _service
    with _service_lock:
        old = _service
        _service = svc
        return old


__all__ = [
    "OnDemandReflectionService",
    "ReflectionEvent",
    "get_on_demand_reflection_service",
    "set_on_demand_reflection_service",
]
