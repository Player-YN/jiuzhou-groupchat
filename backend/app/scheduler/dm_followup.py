"""DM follow-up cron — proactive private messages after long idle.

Architecture:

1. APScheduler ``AsyncIOScheduler`` drives an interval job that fires every
   ``XZ_DM_FOLLOWUP_INTERVAL_HOUR`` hours (default 1).
2. On each fire we scan ``AgentMemoryStore`` for ``(session_id, agent_key)``
   pairs whose latest entry timestamp is older than ``XZ_DM_FOLLOWUP_IDLE_HOUR``
   hours (default 24) AND that contain at least one ``dm`` source entry
   (the user has at least one private conversation with that NPC).
3. For each idle ``(sid, role)`` pair, throttle-check via
   ``CronState.should_throttle_dm`` (default 1h minimum gap between
   follow-ups for the same pair).
4. Drive the NPC to produce a short follow-up message by calling
   ``app.graph._stream_via_letta`` (the same leaf used by the DM
   pipeline) with a system-style prompt that references the agent's
   prior topics from its memory timeline.
5. Persist the reply back into ``AgentMemoryStore`` with ``source="dm"``
   so the next ``dm_init`` handshake surfaces it.
6. Catch ANY exception in the job body and degrade gracefully — the
   scheduler itself never crashes.

Public surface:

- ``DmFollowupService``
- ``scan_idle_pairs()`` — exposed for tests
"""
from __future__ import annotations

import logging
import os
import time
from typing import TYPE_CHECKING, Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.graph import ROLES, _stream_via_letta
from app.letta_bridge.agent_manager import ROLE_AGENT_KEYS
from app.scheduler.state import CronState, get_cron_state

if TYPE_CHECKING:
    from apscheduler.schedulers.asyncio import AsyncIOScheduler


logger = logging.getLogger(__name__)


# ============================================================================
# Constants
# ============================================================================

# Minimum / maximum interval (hours)
_MIN_INTERVAL_HOUR: float = 0.016  # ~1 minute; useful for tests
_MAX_INTERVAL_HOUR: float = 168.0   # 1 week
_DEFAULT_INTERVAL_HOUR: float = 1.0

# Minimum / maximum idle threshold (hours)
_MIN_IDLE_HOUR: float = 0.016
_MAX_IDLE_HOUR: float = 720.0      # 30 days
_DEFAULT_IDLE_HOUR: float = 24.0

# Same-pair throttle — 1h minimum gap between DM follow-ups.
_THROTTLE_SECONDS: float = 3600.0

# DM follow-up prompt
_DM_FOLLOWUP_PROMPT: str = (
    "[system] 你已经很久没主动联系这位道长了。"
    "请根据上次的话题自然地问候一下，问问修行近况。1-2 句话即可。"
)


# ============================================================================
# Service
# ============================================================================


class DmFollowupService:
    """DM proactive follow-up service.

    Lifecycle mirrors ``XiuzhenCronService`` — ``start()`` / ``stop()`` /
    ``trigger_now()``.  ``scan_idle_pairs()`` is exposed so tests can verify
    the scanner logic without waiting for the scheduler to fire.
    """

    def __init__(
        self,
        *,
        state: CronState | None = None,
        interval_hour: float | None = None,
        idle_hour: float | None = None,
        scheduler: "AsyncIOScheduler | None" = None,
    ) -> None:
        self.state = state if state is not None else get_cron_state()
        self.scheduler = scheduler  # lazy

        if interval_hour is None:
            interval_hour = self._read_interval_hour_env()
        self.interval_hour: float = self._clamp_interval(interval_hour)

        if idle_hour is None:
            idle_hour = self._read_idle_hour_env()
        self.idle_hour: float = self._clamp_idle(idle_hour)

    # ------------------------------------------------------------------
    # env / config helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _read_interval_hour_env() -> float:
        raw = os.environ.get("XZ_DM_FOLLOWUP_INTERVAL_HOUR")
        if not raw:
            return _DEFAULT_INTERVAL_HOUR
        try:
            return float(raw)
        except (TypeError, ValueError):
            logger.warning("[dm_followup] bad XZ_DM_FOLLOWUP_INTERVAL_HOUR=%r; defaulting", raw)
            return _DEFAULT_INTERVAL_HOUR

    @staticmethod
    def _read_idle_hour_env() -> float:
        raw = os.environ.get("XZ_DM_FOLLOWUP_IDLE_HOUR")
        if not raw:
            return _DEFAULT_IDLE_HOUR
        try:
            return float(raw)
        except (TypeError, ValueError):
            logger.warning("[dm_followup] bad XZ_DM_FOLLOWUP_IDLE_HOUR=%r; defaulting", raw)
            return _DEFAULT_IDLE_HOUR

    @staticmethod
    def _clamp_interval(value: float) -> float:
        if value < _MIN_INTERVAL_HOUR:
            return _MIN_INTERVAL_HOUR
        if value > _MAX_INTERVAL_HOUR:
            return _MAX_INTERVAL_HOUR
        return value

    @staticmethod
    def _clamp_idle(value: float) -> float:
        if value < _MIN_IDLE_HOUR:
            return _MIN_IDLE_HOUR
        if value > _MAX_IDLE_HOUR:
            return _MAX_IDLE_HOUR
        return value

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------
    def start(self) -> None:
        if self.scheduler is None:
            self.scheduler = AsyncIOScheduler(timezone="UTC")
        if self.scheduler.running:
            return
        # Convert hours to minutes for APScheduler (which expects minutes for IntervalTrigger)
        interval_minutes = max(1, int(self.interval_hour * 60))
        self.scheduler.add_job(
            self._fire_once,
            trigger=IntervalTrigger(minutes=interval_minutes),
            id="dm_followup_fire",
            name="dm_followup_fire",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
            next_run_time=None,
        )
        try:
            self.scheduler.start()
            logger.info(
                "[dm_followup] started: interval=%.2f h idle_threshold=%.2f h",
                self.interval_hour, self.idle_hour,
            )
        except RuntimeError as exc:
            # No running event loop (TestClient / import-time path) — defer
            # to the first call to _fire_once (always async).
            logger.warning(
                "[dm_followup] scheduler.start() deferred (no event loop yet): %s",
                exc,
            )

    def stop(self) -> None:
        if self.scheduler is None or not self.scheduler.running:
            return
        try:
            self.scheduler.shutdown(wait=False)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[dm_followup] scheduler.shutdown raised: %s", exc)
        finally:
            logger.info("[dm_followup] stopped")

    def is_running(self) -> bool:
        return bool(self.scheduler and self.scheduler.running)

    def next_fire_time(self) -> str | None:
        if not self.is_running():
            return None
        job = self.scheduler.get_job("dm_followup_fire")
        if job is None or job.next_run_time is None:
            return None
        return job.next_run_time.isoformat()

    # ------------------------------------------------------------------
    # admin controls
    # ------------------------------------------------------------------
    def set_interval_hour(self, interval_hour: float) -> None:
        new_value = self._clamp_interval(float(interval_hour))
        self.interval_hour = new_value
        if self.scheduler is not None and self.scheduler.running:
            interval_minutes = max(1, int(new_value * 60))
            self.scheduler.reschedule_job(
                "dm_followup_fire",
                trigger=IntervalTrigger(minutes=interval_minutes),
            )

    def set_idle_hour(self, idle_hour: float) -> None:
        self.idle_hour = self._clamp_idle(float(idle_hour))

    # ------------------------------------------------------------------
    # scan + fire (public for tests)
    # ------------------------------------------------------------------
    async def trigger_now(self) -> dict[str, Any]:
        """Run one full scan + fire cycle.  Returns a summary dict."""
        await self._ensure_started()
        return await self._fire_once()

    async def _ensure_started(self) -> None:
        """Start the scheduler lazily on first fire (handles TestClient path)."""
        if self.scheduler is None:
            self.scheduler = AsyncIOScheduler(timezone="UTC")
            interval_minutes = max(1, int(self.interval_hour * 60))
            self.scheduler.add_job(
                self._fire_once,
                trigger=IntervalTrigger(minutes=interval_minutes),
                id="dm_followup_fire",
                name="dm_followup_fire",
                replace_existing=True,
                max_instances=1,
                coalesce=True,
            )
        if self.scheduler.running:
            return
        try:
            self.scheduler.start()
        except RuntimeError:
            logger.warning("[dm_followup] could not start scheduler in async context")

    def scan_idle_pairs(self, *, now: float | None = None) -> list[tuple[str, str]]:
        """Return the list of ``(session_id, agent_key)`` pairs eligible for DM follow-up.

        Public so tests can assert against it without waiting on the scheduler.

        Eligibility criteria (ALL must hold):
          1. The pair has at least one DM entry (source='dm', role='user').
          2. The latest entry timestamp is older than ``idle_hour`` ago.
          3. The pair is not throttled (default 1h since last follow-up).
        """
        from app.memory import get_agent_memory_store

        if now is None:
            now = time.time()

        store = get_agent_memory_store()
        idle_threshold_ms = int((now - self.idle_hour * 3600) * 1000)

        pairs: list[tuple[str, str]] = []
        # Scan all (session_id, agent_key) pairs via a small helper on the store.
        for agent_key in ROLE_AGENT_KEYS:
            try:
                sessions = store.list_sessions_for_agent(agent_key)
            except Exception as exc:  # noqa: BLE001
                logger.warning("[dm_followup] list_sessions_for_agent(%s) failed: %s", agent_key, exc)
                continue
            for session_id in sessions:
                try:
                    entries = store.load_agent_memory(session_id=session_id, agent_key=agent_key)
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "[dm_followup] load_agent_memory(%s, %s) failed: %s",
                        session_id, agent_key, exc,
                    )
                    continue
                if not entries:
                    continue
                # require at least one DM user message — otherwise it's pure group chat noise
                if not any(e.source == "dm" and e.role == "user" for e in entries):
                    continue
                latest_ts = max(e.timestamp for e in entries)
                if latest_ts >= idle_threshold_ms:
                    continue
                if self.state.should_throttle_dm(
                    session_id, agent_key, _THROTTLE_SECONDS, now=now,
                ):
                    continue
                pairs.append((session_id, agent_key))
        return pairs

    async def _fire_once(self) -> dict[str, Any]:
        """Scan idle pairs + fire follow-ups.  Returns a summary dict."""
        if not self.state.enabled:
            return {"event": "skipped", "reason": "disabled"}

        try:
            idle_pairs = self.scan_idle_pairs()
        except Exception as exc:  # noqa: BLE001
            err = f"{type(exc).__name__}: {exc}"
            logger.warning("[dm_followup] scan_idle_pairs failed: %s", err)
            return {"event": "error", "stage": "scan", "message": err}

        if not idle_pairs:
            return {"event": "skipped", "reason": "no_idle_pair"}

        # Process up to N pairs per tick (default 3) to avoid LLM spike.
        max_per_tick = 3
        targets = idle_pairs[:max_per_tick]

        succeeded = 0
        failed = 0
        for session_id, role_key in targets:
            ok = await self._followup_one(session_id=session_id, role_key=role_key)
            if ok:
                succeeded += 1
            else:
                failed += 1

        return {
            "event": "fired",
            "idle_found": len(idle_pairs),
            "targeted": len(targets),
            "succeeded": succeeded,
            "failed": failed,
        }

    async def _followup_one(self, *, session_id: str, role_key: str) -> bool:
        """Generate and persist a follow-up message for one pair.

        Returns True iff the message was successfully written to the store.
        """
        from langchain_core.messages import HumanMessage, SystemMessage

        from app.memory import get_agent_memory_store

        try:
            role = ROLES[role_key]
        except KeyError:
            err = f"unknown role_key={role_key!r}"
            self.state.record_dm_fire(session_id, role_key, ok=False, error=err)
            return False

        system_msg = SystemMessage(content=role["system"])
        user_msg = HumanMessage(content=_DM_FOLLOWUP_PROMPT)
        full_text = ""
        try:
            async for piece in _stream_via_letta(
                role_key=role_key,
                session_id=session_id,
                all_msgs=[system_msg, user_msg],
            ):
                if not piece:
                    continue
                full_text += piece
        except Exception as exc:  # noqa: BLE001
            err = f"{type(exc).__name__}: {exc}"
            logger.warning(
                "[dm_followup] _stream_via_letta failed for (%s, %s): %s",
                session_id, role_key, err,
            )
            self.state.record_dm_fire(session_id, role_key, ok=False, error=err)
            return False

        # Persist to DM timeline
        try:
            store = get_agent_memory_store()
            store.append_message(
                session_id=session_id,
                agent_key=role_key,
                role="agent",
                source="dm",
                speaker_key=role_key,
                text=full_text or "(empty)",
                agent_name=role["name"],
                agent_emoji=role["emoji"],
            )
        except Exception as exc:  # noqa: BLE001
            err = f"{type(exc).__name__}: {exc}"
            logger.warning(
                "[dm_followup] append_message failed for (%s, %s): %s",
                session_id, role_key, err,
            )
            self.state.record_dm_fire(session_id, role_key, ok=False, error=err)
            return False

        self.state.record_dm_fire(session_id, role_key, ok=True)
        logger.info(
            "[dm_followup] fired %s for (%s) — %d chars",
            role_key, session_id, len(full_text),
        )
        return True

    # ------------------------------------------------------------------
    # debug
    # ------------------------------------------------------------------
    def status_dict(self) -> dict[str, Any]:
        return {
            "running": self.is_running(),
            "interval_hour": self.interval_hour,
            "idle_hour": self.idle_hour,
            "next_fire_time": self.next_fire_time(),
            "fire_count": self.state.dm_fire_count,
            "last_error": self.state.last_error,
        }


# ============================================================================
# Module-level singleton accessor
# ============================================================================
_dm_service: DmFollowupService | None = None


def get_dm_followup_service() -> DmFollowupService:
    global _dm_service
    if _dm_service is None:
        _dm_service = DmFollowupService()
    return _dm_service


def set_dm_followup_service(svc: DmFollowupService | None) -> DmFollowupService | None:
    global _dm_service
    old = _dm_service
    _dm_service = svc
    return old


__all__ = [
    "DmFollowupService",
    "get_dm_followup_service",
    "set_dm_followup_service",
]