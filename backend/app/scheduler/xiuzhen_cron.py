"""九洲一号群 group cron — randomly picks 1 of 6 NPC to post a one-liner.

Architecture (Stage 8 Cron):

1. APScheduler ``AsyncIOScheduler`` drives a single ``IntervalTrigger`` job
   that fires every ``XZ_CRON_INTERVAL_MIN`` minutes (default 5, clamped to
   1..1440).
2. Each fire:
   - Resolve the set of eligible NPCs (``npc_filter`` whitelist or all 6).
   - Drop NPCs that throttled within the last ``throttle_sec`` seconds
     (default 3600 = 1h, enforced via ``CronState.should_throttle``).
   - Pick 1 uniformly at random.  Empty pool → skip.
   - Build an isolated ``AllMessages = [SystemMessage(persona), HumanMessage("[system] 你想跟群里说点啥？")]``
     payload and call ``app.graph._stream_via_letta(role_key=...)``.
   - Concatenate streamed chunks into ``full_text``.
   - Fan out the agent's reply to all 6 NPC memories via
     ``AgentMemoryStore.fan_out_group_event`` (so other NPCs see the
     event in their timeline).
   - If any WebSocket is connected (``ConnectionRegistry.active_sessions``),
     push the event via the cron-specific ``cron_agent_msg`` message type
     so the frontend can render it without colliding with the standard
     ``agent_msg_chunk`` protocol used by manual group chat.
   - Catch ANY exception inside the job body and degrade gracefully — the
     scheduler itself never crashes.

Public surface:

- ``XiuzhenCronService`` — owns the scheduler + job + state

The service is constructible standalone (for tests) and also installed
into the FastAPI lifespan via ``app.scheduler.lifespan.start_scheduler``.
"""
from __future__ import annotations

import logging
import os
import random
from typing import TYPE_CHECKING, Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.graph import ROLES, _stream_via_letta
from app.letta_bridge.agent_manager import ROLE_AGENT_KEYS
from app.models import make_msg
from app.scheduler.connection_registry import get_connection_registry
from app.scheduler.state import CronState, get_cron_state

if TYPE_CHECKING:
    from apscheduler.schedulers.asyncio import AsyncIOScheduler


logger = logging.getLogger(__name__)


# ============================================================================
# Constants
# ============================================================================

# Minimum / maximum cron interval (env-clamped)
_MIN_INTERVAL_MIN: int = 1
_MAX_INTERVAL_MIN: int = 1440    # 24h
_DEFAULT_INTERVAL_MIN: int = 5

# 1h same-NPC throttle — comment this in code so future maintainers see it.
_THROTTLE_SECONDS: float = 3600.0

# The fixed system prompt prefix injected to the chosen NPC.  Matches the
# user-facing message we want the NPC to internalise when producing a
# proactive one-liner.
_CRON_USER_MSG: str = "[system] 你想跟群里说点啥？"
_CRON_USER_MSG_DM: str = "[system] 你最近在忙什么？要不要找用户聊聊？"  # reserved

# Wire-level event type emitted to active WS clients.
_CRON_EVENT_TYPE: str = "cron_agent_post"


# ============================================================================
# Service
# ============================================================================


class XiuzhenCronService:
    """九洲一号群 proactive post service.

    Lifecycle:

    - ``__init__`` reads env to derive ``interval_min`` and the initial
      ``enabled`` flag.  No scheduler is created until ``start()`` is called.
    - ``start()`` builds the ``AsyncIOScheduler``, adds the interval job,
      and starts it.
    - ``stop()`` shuts down the scheduler; idempotent.
    - ``trigger_now()`` — synchronous helper that runs the fire-once body
      once; used by tests and admin endpoints.
    """

    def __init__(
        self,
        *,
        state: CronState | None = None,
        interval_min: int | None = None,
        enabled: bool | None = None,
        npc_filter: list[str] | None = None,
        scheduler: "AsyncIOScheduler | None" = None,
    ) -> None:
        # Allow DI for tests; production wires defaults.
        self.state = state if state is not None else get_cron_state()
        self.scheduler = scheduler  # lazy

        if interval_min is None:
            interval_min = self._read_interval_min_env()
        self.interval_min: int = self._clamp_interval(interval_min)

        if enabled is None:
            enabled = self._read_enabled_env()
        self.state.set_enabled(enabled)

        if npc_filter is not None:
            self.state.set_npc_filter(npc_filter)

    # ------------------------------------------------------------------
    # env / config helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _read_interval_min_env() -> int:
        raw = os.environ.get("XZ_CRON_INTERVAL_MIN")
        if not raw:
            return _DEFAULT_INTERVAL_MIN
        try:
            return int(raw)
        except (TypeError, ValueError):
            logger.warning("[cron] bad XZ_CRON_INTERVAL_MIN=%r; using default", raw)
            return _DEFAULT_INTERVAL_MIN

    @staticmethod
    def _clamp_interval(value: int) -> int:
        if value < _MIN_INTERVAL_MIN:
            logger.warning("[cron] interval %d below min %d; clamping", value, _MIN_INTERVAL_MIN)
            return _MIN_INTERVAL_MIN
        if value > _MAX_INTERVAL_MIN:
            logger.warning("[cron] interval %d above max %d; clamping", value, _MAX_INTERVAL_MIN)
            return _MAX_INTERVAL_MIN
        return value

    @staticmethod
    def _read_enabled_env() -> bool:
        raw = os.environ.get("XZ_CRON_ENABLED", "true").strip().lower()
        return raw not in ("false", "0", "off", "no", "n", "")

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------
    def start(self) -> None:
        """Register the job and (best-effort) start the scheduler.

        APScheduler's ``AsyncIOScheduler.start()`` calls
        ``asyncio.get_running_loop()`` — this fails if ``start()`` is called
        from a sync context outside an event loop.  We catch that here and
        defer to the first call to ``_fire_once`` (which IS async and thus
        always inside a loop).
        """
        if self.scheduler is None:
            self.scheduler = AsyncIOScheduler(timezone="UTC")
        if self.scheduler.running:
            logger.info("[cron] scheduler already running; not starting again")
            return
        self.scheduler.add_job(
            self._fire_once,
            trigger=IntervalTrigger(minutes=self.interval_min),
            id="xiuzhen_cron_fire",
            name="xiuzhen_cron_fire",
            replace_existing=True,
            max_instances=1,            # one fire at a time
            coalesce=True,              # coalesce missed runs into one
            next_run_time=None,         # don't fire immediately on start
        )
        try:
            self.scheduler.start()
            logger.info(
                "[cron] xiuzhen cron started: interval=%d min enabled=%s",
                self.interval_min, self.state.enabled,
            )
        except RuntimeError as exc:
            # No running event loop (TestClient / import-time path) — defer
            # to the first call to _fire_once (always async).
            logger.warning(
                "[cron] xiuzhen scheduler.start() deferred (no event loop yet): %s",
                exc,
            )

    def stop(self) -> None:
        """Stop the scheduler; idempotent."""
        if self.scheduler is None or not self.scheduler.running:
            return
        try:
            self.scheduler.shutdown(wait=False)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[cron] scheduler.shutdown raised: %s", exc)
        finally:
            logger.info("[cron] xiuzhen cron stopped")

    def is_running(self) -> bool:
        return bool(self.scheduler and self.scheduler.running)

    def next_fire_time(self) -> str | None:
        """Return the next-fire time as ISO 8601 string (or None if not scheduled)."""
        if not self.is_running():
            return None
        job = self.scheduler.get_job("xiuzhen_cron_fire")
        if job is None or job.next_run_time is None:
            return None
        return job.next_run_time.isoformat()

    # ------------------------------------------------------------------
    # admin controls
    # ------------------------------------------------------------------
    def set_enabled(self, enabled: bool) -> None:
        """Enable / disable the cron without restarting the process."""
        self.state.set_enabled(enabled)
        # Also support clearing the filter as a no-op fast path
        if self.scheduler is not None and self.scheduler.running:
            if enabled:
                self.scheduler.resume_job("xiuzhen_cron_fire")
            else:
                self.scheduler.pause_job("xiuzhen_cron_fire")

    def set_npc_filter(self, npc_filter: list[str] | None) -> None:
        """Restrict the cron to a whitelist of NPCs (None = all 6)."""
        # Validate against canonical 6 keys; drop unknown silently.
        if npc_filter is None:
            self.state.set_npc_filter(None)
            return
        valid = [n for n in npc_filter if n in ROLE_AGENT_KEYS]
        self.state.set_npc_filter(valid if valid else None)

    def set_interval_min(self, interval_min: int) -> None:
        """Reschedule the cron with a new interval (live, no restart)."""
        new_value = self._clamp_interval(int(interval_min))
        self.interval_min = new_value
        if self.scheduler is not None and self.scheduler.running:
            self.scheduler.reschedule_job(
                "xiuzhen_cron_fire",
                trigger=IntervalTrigger(minutes=new_value),
            )
            logger.info("[cron] interval rescheduled to %d min", new_value)

    # ------------------------------------------------------------------
    # core fire logic (public for tests)
    # ------------------------------------------------------------------
    async def trigger_now(self) -> dict[str, Any]:
        """Fire the cron job once.  Returns a summary dict for tests/admin."""
        await self._ensure_started()
        return await self._fire_once()

    async def _ensure_started(self) -> None:
        """Start the scheduler lazily on first fire (handles TestClient path)."""
        if self.scheduler is None:
            self.scheduler = AsyncIOScheduler(timezone="UTC")
            self.scheduler.add_job(
                self._fire_once,
                trigger=IntervalTrigger(minutes=self.interval_min),
                id="xiuzhen_cron_fire",
                name="xiuzhen_cron_fire",
                replace_existing=True,
                max_instances=1,
                coalesce=True,
            )
        if self.scheduler.running:
            return
        try:
            self.scheduler.start()
        except RuntimeError:
            # Even inside an async context, if start() fails we just log and
            # continue — the body of _fire_once doesn't need the scheduler
            # to be running (it uses trigger_now semantics).
            logger.warning("[cron] could not start scheduler in async context")

    async def _fire_once(self) -> dict[str, Any]:
        """Pick 1 NPC, run _stream_via_letta, fan-out + push to WS."""
        # Guard: globally disabled?
        if not self.state.enabled:
            return {"event": "skipped", "reason": "disabled"}

        # 1) eligible pool (npc_filter ∩ ROLE_AGENT_KEYS \ throttled)
        pool = self._eligible_pool()
        if not pool:
            return {"event": "skipped", "reason": "no_eligible_npc"}

        # 2) random pick — uniform over the pool
        role_key = random.choice(pool)

        # 3) drive the NPC to produce a one-liner via _stream_via_letta
        from langchain_core.messages import HumanMessage, SystemMessage

        from app.memory import get_agent_memory_store

        try:
            role = ROLES[role_key]
        except KeyError:
            err = f"unknown role_key={role_key!r}"
            self.state.record_group_fire(role_key, ok=False, error=err)
            return {"event": "error", "message": err}

        system_msg = SystemMessage(content=role["system"])
        user_msg = HumanMessage(content=_CRON_USER_MSG)
        full_text = ""
        chunk_count = 0
        try:
            async for piece in _stream_via_letta(
                role_key=role_key,
                session_id=_cron_session_id(role_key),
                all_msgs=[system_msg, user_msg],
            ):
                if not piece:
                    continue
                full_text += piece
                chunk_count += 1
        except Exception as exc:  # noqa: BLE001 — graceful degrade
            err = f"{type(exc).__name__}: {exc}"
            logger.warning("[cron] _stream_via_letta failed for %s: %s", role_key, err)
            self.state.record_group_fire(role_key, ok=False, error=err)
            return {"event": "error", "role_key": role_key, "message": err}

        # 4) fan out to all 6 NPC memories (group event)
        try:
            store = get_agent_memory_store()
            store.fan_out_group_event(
                session_id=_cron_session_id(role_key),
                speaker_key=role_key,
                role="agent",
                text=full_text or "(empty)",
                agent_name=role["name"],
                agent_emoji=role["emoji"],
                audience=list(ROLE_AGENT_KEYS),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("[cron] fan_out_group_event failed for %s: %s", role_key, exc)

        # 5) push to active WS sessions (if any)
        pushed_to = await self._push_to_active_ws(
            role_key=role_key, full_text=full_text,
        )

        # 6) bookkeeping
        self.state.record_group_fire(role_key, ok=True)
        logger.info(
            "[cron] fired %s (%d chunks, %d ws, text=%d chars)",
            role_key, chunk_count, pushed_to, len(full_text),
        )
        return {
            "event": "fired",
            "role_key": role_key,
            "chunks": chunk_count,
            "text_len": len(full_text),
            "ws_pushed": pushed_to,
            "text_preview": full_text[:60],
        }

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    def _eligible_pool(self) -> list[str]:
        """Build the list of NPCs allowed to fire this tick."""
        # start with canonical 6 (or filtered whitelist)
        if self.state.npc_filter:
            base = [n for n in self.state.npc_filter if n in ROLE_AGENT_KEYS]
        else:
            base = list(ROLE_AGENT_KEYS)
        # drop throttled NPCs
        return [n for n in base if not self.state.should_throttle(n, _THROTTLE_SECONDS)]

    async def _push_to_active_ws(self, role_key: str, full_text: str) -> int:
        """Push the cron event to every connected WS session.  Return count pushed."""
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
            # Build a fresh per-session dict (don't reuse — mocking test
            # layers capture the dict by reference and would see later mutations).
            msg = make_msg(
                _CRON_EVENT_TYPE,
                session_id=sid,
                role_key=role_key,
                name=role["name"],
                emoji=role["emoji"],
                full_text=full_text,
            )
            try:
                await ws.send_json(msg)
                pushed += 1
            except Exception as exc:  # noqa: BLE001 — don't break the loop on one bad socket
                logger.warning("[cron] ws send failed for sid=%s: %s", sid, exc)
        return pushed

    # ------------------------------------------------------------------
    # debug
    # ------------------------------------------------------------------
    def status_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly status dict (used by /api/cron/status)."""
        return {
            "running": self.is_running(),
            "interval_min": self.interval_min,
            "enabled": self.state.enabled,
            "npc_filter": list(self.state.npc_filter) if self.state.npc_filter else None,
            "next_fire_time": self.next_fire_time(),
            "fire_count": self.state.group_fire_count,
            "last_error": self.state.last_error,
        }


def _cron_session_id(role_key: str) -> str:
    """Build a deterministic session_id for cron posts.

    Why deterministic?  It groups all cron posts from the same NPC into
    one timeline (lets us look at "all of 白前辈's cron posts ever" easily
    via ``list_sessions_for_agent``).
    """
    return f"cron-{role_key}"


# ============================================================================
# Module-level singleton accessor (used by lifespan + admin endpoint)
# ============================================================================
_xiuzhen_service: XiuzhenCronService | None = None


def get_xiuzhen_cron_service() -> XiuzhenCronService:
    """Return the process-wide ``XiuzhenCronService`` (lazy init)."""
    global _xiuzhen_service
    if _xiuzhen_service is None:
        _xiuzhen_service = XiuzhenCronService()
    return _xiuzhen_service


def set_xiuzhen_cron_service(svc: XiuzhenCronService | None) -> XiuzhenCronService | None:
    """Replace (or clear) the singleton.  Returns the previous instance."""
    global _xiuzhen_service
    old = _xiuzhen_service
    _xiuzhen_service = svc
    return old


__all__ = [
    "XiuzhenCronService",
    "get_xiuzhen_cron_service",
    "set_xiuzhen_cron_service",
]