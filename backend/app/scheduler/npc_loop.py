"""Per-NPC autonomous loop — ADR-0007 Option B.

Stage 8-NPC-Love (Group Chat "I'd like to chime in" architecture).  This
module replaces the mechanical ``XiuzhenCronService`` (every 5 min pick 1
NPC and push a canned line) with **6 autonomous agentic loops**, one per
NPC, each reading recent group context and deciding for itself whether
to speak.

Loop rhythm (per NPC, per iteration):

1.  THINK — read up to 20 most recent group events from ``AgentMemoryStore``
    (via ``load_recent_group_events``).  Build a context-aware decision
    prompt that includes persona + recent events + a ``<silent/>`` token
    instruction.

2.  DECIDE — call ``stream_via_letta_with_retry`` (Letta retry wrapper) with
    the decision prompt.  Parse the response:

    - if ``<silent/>`` token found → NPC wants to stay quiet → short sleep.
    - else → full_text is the NPC's reply → proceed to ACT.

3.  ACT — acquire the global ``GroupChatSemaphore`` (single-flight + 10s
    cool-down).  If cool-down violated → bail out, sleep a bit, retry next
    iteration.  Otherwise fan out the reply to all 6 NPC memories
    (``AgentMemoryStore.fan_out_group_event``) and push a
    ``cron_agent_post`` event to all active WS sessions.

4.  SLEEP — random 60-300s after a successful push, or 30-120s after a
    silent decision.  Random sleep makes the 6 NPCs desynchronise so they
    don't all wake up at the same wall-clock time.

5.  REVIVE — any exception in the loop body is caught, logged, and the
    loop sleeps 60s before re-entering.  A dead loop can be detected
    externally via ``loop.is_alive()``.

Public surface:

- ``NpcLoop``            — dataclass holding one NPC's loop state
- ``NpcLoopPool``        — owns 6 NpcLoop instances + lifecycle
- ``get_npc_loop_pool()`` / ``set_npc_loop_pool()``
- ``start_all()`` / ``stop_all()`` — process-wide lifecycle (used by lifespan)
"""
from __future__ import annotations

import asyncio
import logging
import random
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from app.graph import ROLES
from app.letta_bridge.agent_manager import ROLE_AGENT_KEYS
from app.models import make_msg
from app.scheduler.connection_registry import get_connection_registry
from app.scheduler.group_semaphore import get_group_semaphore
from app.scheduler.letta_retry import (
    LettaRetryExhausted,
    stream_via_letta_with_retry,
)
from app.scheduler.state import CronState, get_cron_state


logger = logging.getLogger(__name__)


# ============================================================================
# Constants
# ============================================================================
# Random sleep ranges (seconds) — ADR-0007 §Option B pseudocode:
_SLEEP_AFTER_PUSH_MIN: float = 60.0
_SLEEP_AFTER_PUSH_MAX: float = 300.0
_SLEEP_AFTER_SILENT_MIN: float = 30.0
_SLEEP_AFTER_SILENT_MAX: float = 120.0
_SLEEP_AFTER_EXCEPTION_SEC: float = 60.0

# On LettaRetryExhausted we sleep 5 minutes before letting this NPC try again
# (ADR-0007 §Option B scope item 3).
_SLEEP_AFTER_RETRY_EXHAUSTED_SEC: float = 300.0

# Context window size — how many recent group events to feed into the decision
# prompt.  ADR-0007 spec: "20 recent events".
_RECENT_EVENTS_LIMIT: int = 20

# <silent/> token — the magic word the NPC emits to opt out of a turn.
SILENT_TOKEN: str = "<silent/>"

# Session id pattern used for NPC-loop self-driven posts.  Distinct from
# ``cron-{role_key}`` (which is used by the legacy cron service) so we don't
# collide in AgentMemoryStore.
def _loop_session_id(role_key: str) -> str:
    return f"loop-{role_key}"


# Decision prompt template.  Includes:
# - persona role reminder
# - 20 most-recent group events (or "(群聊暂无消息)" if empty)
# - <silent/> token instruction
# - 1-2 sentence length guidance (kept short to control cost)
_DECISION_PROMPT_FMT: str = (
    "你是【{name}】（{key}，{emoji}）。你现在身处九洲一号群中。\n"
    "以下是该群最近 {limit} 条公开消息（按时间从早到晚）：\n"
    "---\n"
    "{events}\n"
    "---\n"
    "请你判断：此刻你想主动插一句话吗？\n"
    "如果不想说话、或者觉得没必要硬接话，**只回复** ``{silent_token}``（不要附加任何其他字符）。\n"
    "如果想插话，请用你的角色口吻**简短**说 1-2 句话（不超过 80 字），不要 @ 别人、不要长篇分析、不要客套开场白。\n"
    "只需要输出：要么 ``{silent_token}``，要么你的发言本身，不要再加任何解释。"
)


# ============================================================================
# Data classes
# ============================================================================
@dataclass
class NpcLoop:
    """State for one NPC's autonomous loop.

    The ``task`` field holds the ``asyncio.Task`` returned by
    ``asyncio.create_task(_npc_loop(role_key))``.  ``last_spoke_at`` records
    the last time this NPC pushed a message (epoch seconds) — used for
    per-NPC introspection + tests.

    ``stop_event`` is set by ``stop_all()`` to ask the loop to exit at its
    next sleep boundary (no forced cancel).
    """

    role_key: str
    task: asyncio.Task | None = None
    last_spoke_at: float = 0.0
    total_decisions: int = 0
    silent_decisions: int = 0
    pushed_decisions: int = 0
    last_error: str | None = None
    stop_event: asyncio.Event = field(default_factory=asyncio.Event)

    # ------------------------------------------------------------------
    def is_alive(self) -> bool:
        """Return True iff the loop task is currently running."""
        return self.task is not None and not self.task.done()

    def status_dict(self) -> dict[str, Any]:
        """JSON-friendly view (used by /api/cron/status)."""
        return {
            "role_key": self.role_key,
            "alive": self.is_alive(),
            "last_spoke_at": self.last_spoke_at,
            "total_decisions": self.total_decisions,
            "silent_decisions": self.silent_decisions,
            "pushed_decisions": self.pushed_decisions,
            "last_error": self.last_error,
        }


# ============================================================================
# Pool (6 NpcLoops + lifecycle)
# ============================================================================
class NpcLoopPool:
    """Owns 6 ``NpcLoop`` instances and manages their lifecycle.

    Lifecycle:

    - ``start_all()`` — spawns one ``_npc_loop`` task per NPC; idempotent.
    - ``stop_all()``  — sets each loop's stop_event then awaits cancellation;
      idempotent.
    - ``trigger_one(role_key)`` — async helper that runs ONE full
      decision-then-act cycle for ``role_key`` and returns a summary dict.
      Used by tests and admin endpoints (``POST /api/cron/trigger?target=loop``).
    - ``is_running()`` — returns True iff at least one loop task is alive.
    """

    def __init__(
        self,
        *,
        state: CronState | None = None,
        recent_events_limit: int = _RECENT_EVENTS_LIMIT,
    ) -> None:
        self.state = state if state is not None else get_cron_state()
        self.recent_events_limit = int(recent_events_limit)
        # Per-NPC dataclass instances; lazy-instantiated as tasks are started
        self._loops: dict[str, NpcLoop] = {
            role_key: NpcLoop(role_key=role_key) for role_key in ROLE_AGENT_KEYS
        }
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # reads
    # ------------------------------------------------------------------
    def loops(self) -> dict[str, NpcLoop]:
        """Return the live ``role_key → NpcLoop`` dict (do not mutate)."""
        return self._loops

    def get(self, role_key: str) -> NpcLoop | None:
        return self._loops.get(role_key)

    def is_running(self) -> bool:
        return any(loop.is_alive() for loop in self._loops.values())

    def alive_count(self) -> int:
        return sum(1 for loop in self._loops.values() if loop.is_alive())

    def status_dict(self) -> dict[str, Any]:
        """JSON-friendly status for /api/cron/status endpoint."""
        return {
            "running": self.is_running(),
            "alive_count": self.alive_count(),
            "total": len(self._loops),
            "loops": {
                role_key: loop.status_dict()
                for role_key, loop in self._loops.items()
            },
        }

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------
    def start_all(self) -> None:
        """Spawn one autonomous loop per NPC.  Idempotent."""
        with self._lock:
            for role_key, loop in self._loops.items():
                if loop.is_alive():
                    continue
                # Reset stop_event for a fresh start (in case of restart).
                loop.stop_event = asyncio.Event()
                loop.task = asyncio.create_task(
                    _npc_loop(role_key, loop, self),
                    name=f"npc_loop::{role_key}",
                )
                logger.info("[npc_loop] started: %s", role_key)

    async def stop_all(self) -> None:
        """Signal all loops to stop, then await their cancellation.

        Idempotent.  Each loop checks ``stop_event`` at every sleep boundary
        and exits cleanly (returns from ``_npc_loop``).
        """
        with self._lock:
            loops = list(self._loops.values())
        # Signal first (cheap), then await.
        for loop in loops:
            loop.stop_event.set()
        # Wait for each to finish (with a timeout per loop).
        for loop in loops:
            if loop.task is None:
                continue
            try:
                await asyncio.wait_for(loop.task, timeout=10.0)
            except asyncio.TimeoutError:
                logger.warning("[npc_loop] %s did not exit in 10s; cancelling", loop.role_key)
                loop.task.cancel()
                try:
                    await loop.task
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass
            except Exception as exc:  # noqa: BLE001
                logger.warning("[npc_loop] %s raised on shutdown: %s", loop.role_key, exc)
            finally:
                loop.task = None

    # ------------------------------------------------------------------
    # one-shot trigger (used by tests + admin)
    # ------------------------------------------------------------------
    async def trigger_one(self, role_key: str) -> dict[str, Any]:
        """Run ONE full decision→act cycle for ``role_key`` without starting a loop.

        Mirrors the body of ``_npc_loop`` for one iteration, but returns the
        summary dict immediately (does NOT await the post-cycle sleep).
        Used by tests (so they don't have to wait for the 60-300s post-push
        sleep) and by the admin endpoint (``POST /api/cron/trigger``) for ops.

        Returns a summary dict with: ``event`` (pushed | silent | skipped |
        error), ``role_key``, and bookkeeping counters.
        """
        if role_key not in self._loops:
            return {"event": "error", "reason": "unknown_role", "role_key": role_key}
        loop = self._loops[role_key]
        return await _one_cycle_summary(role_key, loop, self)


# ============================================================================
# Core loop coroutine
# ============================================================================
async def _npc_loop(role_key: str, loop: NpcLoop, pool: NpcLoopPool) -> None:
    """The autonomous loop body.

    Runs until ``loop.stop_event`` is set (or the task is cancelled).
    Each iteration:

    1.  Call ``_one_cycle`` (think → decide → act → return sleep duration)
    2.  Sleep for the duration returned (interruptible via stop_event)
    3.  On any exception in the cycle → log + sleep + restart

    Splitting cycle-from-sleep lets ``pool.trigger_one(role_key)`` reuse the
    same cycle body without paying the post-cycle sleep cost in unit tests.
    """
    logger.info("[npc_loop] %s: entering main loop", role_key)
    try:
        while not loop.stop_event.is_set():
            sleep_for = 0.0
            try:
                sleep_for = await _one_cycle(role_key, loop, pool)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — never let loop die silently
                loop.last_error = f"{type(exc).__name__}: {exc}"
                logger.exception("[npc_loop] %s: cycle crashed, will revive in %ss",
                                 role_key, _SLEEP_AFTER_EXCEPTION_SEC)
                sleep_for = _SLEEP_AFTER_EXCEPTION_SEC
            # Sleep with cancellation support so stop_event / cancel
            # takes effect quickly.  ``_one_cycle`` returns a float (seconds).
            if sleep_for and not loop.stop_event.is_set():
                try:
                    await _interruptible_sleep(loop, float(sleep_for))
                except asyncio.CancelledError:
                    raise
    except asyncio.CancelledError:
        logger.info("[npc_loop] %s: cancelled", role_key)
    finally:
        logger.info("[npc_loop] %s: exiting main loop", role_key)


async def _one_cycle(role_key: str, loop: NpcLoop, pool: NpcLoopPool) -> float:
    """One full THINK → DECIDE → ACT iteration.  Returns the sleep duration
    in seconds (caller ``_npc_loop`` will actually await the sleep; tests
    using ``pool.trigger_one(role_key)`` ignore the return value).

    The summary dict that ``trigger_one`` returns to its caller is built in
    a wrapper layer (``_one_cycle_summary``) — this function stays focused
    on the loop body.

    Returns:
        Seconds to sleep before the next iteration.  0.0 = no sleep needed.
    """
    # ----- 1) THINK — load recent group context -----
    from app.memory import get_agent_memory_store

    try:
        store = get_agent_memory_store()
        recent = store.load_recent_group_events(limit=pool.recent_events_limit)
    except Exception as exc:  # noqa: BLE001
        loop.last_error = f"load_recent_group_events: {exc}"
        logger.warning("[npc_loop] %s: failed to load recent events: %s", role_key, exc)
        return _SLEEP_AFTER_EXCEPTION_SEC

    # Format events text (oldest first so LLM reads top-down).
    events_text = _format_events(reversed(recent))

    # ----- 2) DECIDE — call Letta with retry wrapper -----
    role = ROLES[role_key]
    system_msg = SystemMessage(content=role["system"])
    user_msg = HumanMessage(content=_build_decision_prompt(role_key, events_text))
    full_text = ""
    try:
        async for piece in stream_via_letta_with_retry(
            role_key=role_key,
            session_id=_loop_session_id(role_key),
            all_msgs=[system_msg, user_msg],
        ):
            full_text += piece
    except LettaRetryExhausted as exc:
        loop.last_error = f"letta_retry_exhausted: {exc}"
        logger.warning("[npc_loop] %s: retry exhausted — next attempt in %ss",
                       role_key, _SLEEP_AFTER_RETRY_EXHAUSTED_SEC)
        return _SLEEP_AFTER_RETRY_EXHAUSTED_SEC
    except Exception as exc:  # noqa: BLE001
        loop.last_error = f"{type(exc).__name__}: {exc}"
        logger.warning("[npc_loop] %s: letta stream failed: %s", role_key, exc)
        return _SLEEP_AFTER_EXCEPTION_SEC

    loop.total_decisions += 1

    # ----- 3) Parse <silent/> token -----
    if SILENT_TOKEN in full_text:
        loop.silent_decisions += 1
        return random.uniform(_SLEEP_AFTER_SILENT_MIN, _SLEEP_AFTER_SILENT_MAX)

    # ----- 4) ACT — acquire group semaphore + fan-out + push WS -----
    sem = get_group_semaphore()
    async with sem.guard() as ok:
        if not ok:
            # Cool-down violated — another NPC spoke <10s ago.  Bail.
            logger.info("[npc_loop] %s: cool-down active, skipping push", role_key)
            # Short sleep so we don't immediately retry (would just bounce
            # off the cool-down again).
            return min(15.0, sem.cooldown_remaining() + 1.0)
        # Cool-down passed — fan-out + push.
        try:
            await _fan_out(role_key=role_key, full_text=full_text)
            pushed_to = await _push_to_active_ws(role_key=role_key, full_text=full_text)
        except Exception as exc:  # noqa: BLE001
            loop.last_error = f"fan_out_or_push: {exc}"
            logger.exception("[npc_loop] %s: fan-out/push failed", role_key)
            return _SLEEP_AFTER_EXCEPTION_SEC

    # ----- 5) Bookkeeping -----
    loop.pushed_decisions += 1
    loop.last_spoke_at = time.time()
    pool.state.record_group_fire(role_key, ok=True)
    logger.info(
        "[npc_loop] %s: pushed (%d chars, ws=%d)",
        role_key, len(full_text), pushed_to,
    )
    return random.uniform(_SLEEP_AFTER_PUSH_MIN, _SLEEP_AFTER_PUSH_MAX)


# ----------------------------------------------------------------------------
# Test/admin summary wrapper — runs ONE cycle and returns a summary dict
# instead of sleeping.  Mirrors the cycle body but skips the sleep so unit
# tests + admin endpoint don't pay the post-cycle sleep cost.
# ----------------------------------------------------------------------------
async def _one_cycle_summary(role_key: str, loop: NpcLoop, pool: NpcLoopPool) -> dict[str, Any]:
    """Same as ``_one_cycle`` but returns a JSON-friendly summary dict.

    The summary dict has the shape ``trigger_one`` callers expect:
        {"event": "pushed"|"silent"|"skipped"|"error", "role_key": ..., ...}

    The bookkeeping fields (``loop.pushed_decisions`` etc.) are still
    updated in-place so the admin status endpoint reports correct counts.
    """
    from app.memory import get_agent_memory_store

    # Re-implement the cycle inline but capture the result + skip the sleep.
    try:
        store = get_agent_memory_store()
        recent = store.load_recent_group_events(limit=pool.recent_events_limit)
    except Exception as exc:  # noqa: BLE001
        loop.last_error = f"load_recent_group_events: {exc}"
        return {"event": "skipped", "reason": "store_error", "role_key": role_key}

    events_text = _format_events(reversed(recent))
    role = ROLES[role_key]
    system_msg = SystemMessage(content=role["system"])
    user_msg = HumanMessage(content=_build_decision_prompt(role_key, events_text))
    full_text = ""
    try:
        async for piece in stream_via_letta_with_retry(
            role_key=role_key,
            session_id=_loop_session_id(role_key),
            all_msgs=[system_msg, user_msg],
        ):
            full_text += piece
    except LettaRetryExhausted as exc:
        loop.last_error = f"letta_retry_exhausted: {exc}"
        return {"event": "skipped", "reason": "letta_retry_exhausted", "role_key": role_key}
    except Exception as exc:  # noqa: BLE001
        loop.last_error = f"{type(exc).__name__}: {exc}"
        return {"event": "skipped", "reason": "letta_error", "role_key": role_key,
                "message": str(exc)}

    loop.total_decisions += 1

    if SILENT_TOKEN in full_text:
        loop.silent_decisions += 1
        return {
            "event": "silent",
            "role_key": role_key,
            "total_decisions": loop.total_decisions,
            "silent_decisions": loop.silent_decisions,
        }

    sem = get_group_semaphore()
    async with sem.guard() as ok:
        if not ok:
            return {
                "event": "skipped",
                "reason": "cooldown",
                "role_key": role_key,
                "text_preview": full_text[:60],
            }
        try:
            await _fan_out(role_key=role_key, full_text=full_text)
            pushed_to = await _push_to_active_ws(role_key=role_key, full_text=full_text)
        except Exception as exc:  # noqa: BLE001
            loop.last_error = f"fan_out_or_push: {exc}"
            return {
                "event": "error",
                "reason": "fan_out_or_push",
                "role_key": role_key,
                "message": str(exc),
            }

    loop.pushed_decisions += 1
    loop.last_spoke_at = time.time()
    pool.state.record_group_fire(role_key, ok=True)
    return {
        "event": "pushed",
        "role_key": role_key,
        "text_len": len(full_text),
        "ws_pushed": pushed_to,
        "text_preview": full_text[:60],
        "total_decisions": loop.total_decisions,
        "silent_decisions": loop.silent_decisions,
        "pushed_decisions": loop.pushed_decisions,
    }


# ============================================================================
# Helpers
# ============================================================================
def _format_events(events: Any) -> str:
    """Render a list of recent ``AgentMemoryEntry`` as multi-line text.

    Each line: ``[HH:MM] <speaker_key>: <text>``.  Falls back gracefully
    on missing fields.
    """
    lines: list[str] = []
    for ev in events:
        ts = getattr(ev, "timestamp", None)
        try:
            ts_str = time.strftime("%H:%M", time.localtime(int(ts) / 1000.0))
        except Exception:  # noqa: BLE001
            ts_str = "??:??"
        speaker = getattr(ev, "speaker_key", "?") or "?"
        text = (getattr(ev, "text", "") or "").replace("\n", " ").strip()
        if len(text) > 80:
            text = text[:80] + "…"
        lines.append(f"[{ts_str}] {speaker}: {text}")
    if not lines:
        return "(群聊暂无消息)"
    return "\n".join(lines)


def _build_decision_prompt(role_key: str, events_text: str) -> str:
    """Render the context-aware decision prompt for ``role_key``."""
    role = ROLES[role_key]
    return _DECISION_PROMPT_FMT.format(
        name=role["name"],
        key=role_key,
        emoji=role.get("emoji", ""),
        limit=_RECENT_EVENTS_LIMIT,
        events=events_text,
        silent_token=SILENT_TOKEN,
    )


async def _interruptible_sleep(loop: NpcLoop, seconds: float) -> None:
    """Sleep ``seconds`` but wake early if ``loop.stop_event`` is set.

    Polls every 1s — coarse-grained but cheap.  Cancellation propagates
    cleanly via the surrounding ``_npc_loop`` except clause.
    """
    if seconds <= 0:
        return
    end = time.time() + seconds
    while time.time() < end:
        if loop.stop_event.is_set():
            return
        # Sleep in 1s slices so we react quickly to stop_event.
        remaining = end - time.time()
        await asyncio.sleep(min(1.0, remaining))


async def _fan_out(*, role_key: str, full_text: str) -> int:
    """Fan-out the NPC's reply to all 6 NPC memories (group event).

    Returns the number of rows written (== 6 on success).  Raises on
    unrecoverable error.
    """
    from app.memory import get_agent_memory_store

    role = ROLES[role_key]
    store = get_agent_memory_store()
    entries = store.fan_out_group_event(
        session_id=_loop_session_id(role_key),
        speaker_key=role_key,
        role="agent",
        text=full_text or "(empty)",
        agent_name=role["name"],
        agent_emoji=role["emoji"],
        audience=list(ROLE_AGENT_KEYS),
    )
    return len(entries)


async def _push_to_active_ws(*, role_key: str, full_text: str) -> int:
    """Push the cron event to every connected WS session.  Return count pushed.

    Async because it ``await``s per-socket sends inside an event loop.

    Wire-level event type is ``cron_agent_post`` — **do not change** without
    updating the frontend listener (user policy).  The ``source`` field in
    the payload distinguishes ``"npc_loop"`` (this module) from legacy cron
    posts.
    """
    reg = get_connection_registry()
    sessions = reg.active_sessions()
    if not sessions:
        return 0
    role = ROLES[role_key]
    pushed = 0
    for sid in sessions:
        ws = reg.get(sid)
        if ws is None:
            continue
        msg = make_msg(
            "cron_agent_post",   # wire-level event type — do NOT change (user policy)
            session_id=sid,
            role_key=role_key,
            name=role["name"],
            emoji=role["emoji"],
            full_text=full_text,
            source="npc_loop",   # tag so frontend can distinguish legacy cron
        )
        try:
            await ws.send_json(msg)
            pushed += 1
        except Exception as exc:  # noqa: BLE001 — don't break the loop on one bad socket
            logger.warning("[npc_loop] ws send failed for sid=%s: %s", sid, exc)
    return pushed


# ============================================================================
# Module-level singleton accessor
# ============================================================================
_pool: NpcLoopPool | None = None
_pool_lock = threading.Lock()


def get_npc_loop_pool() -> NpcLoopPool:
    """Return the process-wide ``NpcLoopPool`` (lazy init)."""
    global _pool
    with _pool_lock:
        if _pool is None:
            _pool = NpcLoopPool()
        return _pool


def set_npc_loop_pool(pool: NpcLoopPool | None) -> NpcLoopPool | None:
    """Replace (or clear) the singleton.  Returns the previous instance.

    Tests pass a fresh ``NpcLoopPool(state=...)`` to get isolation; production
    code does NOT call this.
    """
    global _pool
    with _pool_lock:
        old = _pool
        _pool = pool
        return old


# ============================================================================
# Module-level convenience: start_all / stop_all
# ============================================================================
def start_all() -> NpcLoopPool:
    """Construct (if needed) + start the pool.  Returns the pool.

    Sync — does NOT await.  Idempotent: a second call returns the same
    pool without re-spawning tasks (per-loop ``is_alive()`` check).
    """
    pool = get_npc_loop_pool()
    pool.start_all()
    return pool


async def stop_all() -> None:
    """Stop the pool (if started).  Idempotent.

    Used by the FastAPI lifespan ``shutdown`` phase.
    """
    pool = get_npc_loop_pool()
    await pool.stop_all()


__all__ = [
    "NpcLoop",
    "NpcLoopPool",
    "get_npc_loop_pool",
    "set_npc_loop_pool",
    "start_all",
    "stop_all",
    "SILENT_TOKEN",
]