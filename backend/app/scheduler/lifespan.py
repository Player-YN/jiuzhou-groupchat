"""Scheduler lifespan integration — start/stop the cron services.

Public surface:

- ``start_scheduler()``        — called from ``app/main.py`` FastAPI lifespan
- ``shutdown_scheduler()``     — counterpart
- ``get_scheduler()``          — admin endpoint helper (returns all 3 services)

The startup sequence:

1. Construct ``XiuzhenCronService`` and ``DmFollowupService`` lazily.
2. Construct ``NpcLoopPool`` (Stage 8-NPC-Love — ADR-0007 Option B).  Per
   env var ``GC_LOOPS_ENABLED`` (default ``true``) the pool is started
   automatically; the legacy cron is left as a toggleable fallback.
3. ``start()`` on each (idempotent).
4. On shutdown, ``stop()`` both.

Env-var contract:

- ``GC_LOOPS_ENABLED`` (default ``true``) — toggle for the new per-NPC loop
  pool.  When ``false``, the loop pool is constructed but not started
  (legacy cron takes over group proactive posting).
- ``XZ_CRON_ENABLED`` — **deprecated alias**.  Kept for backward compat:
  when ``GC_LOOPS_ENABLED`` is not explicitly set, ``XZ_CRON_ENABLED=false``
  disables the loop pool (mirrors the legacy cron toggle).  When both are
  unset, defaults to ``true``.
- ``XZ_CRON_INTERVAL_MIN`` / ``XZ_DM_FOLLOWUP_INTERVAL_HOUR`` /
  ``XZ_DM_FOLLOWUP_IDLE_HOUR`` — still honoured by the legacy services.

Failure mode: any failure to construct or start a service is logged but
does NOT block FastAPI startup — the BFF runs in degraded mode where
group chat + DM still work, just without proactive behaviour.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

from app.scheduler.behavior_coordinator import (
    BehaviorCoordinator,
    get_behavior_coordinator,
)

from app.scheduler.dm_followup import (
    DmFollowupService,
    get_dm_followup_service,
    set_dm_followup_service,
)
from app.scheduler.npc_loop import (
    NpcLoopPool,
    get_npc_loop_pool,
    set_npc_loop_pool,
    stop_all as npc_loop_stop_all,
)
from app.scheduler.xiuzhen_cron import (
    XiuzhenCronService,
    get_xiuzhen_cron_service,
    set_xiuzhen_cron_service,
)

logger = logging.getLogger(__name__)


@dataclass
class SchedulerBundle:
    """Container for all cron / loop services — handy for status endpoints."""

    xiuzhen: XiuzhenCronService
    dm_followup: DmFollowupService
    npc_loop_pool: NpcLoopPool
    behavior_coordinator: BehaviorCoordinator


_bundle: SchedulerBundle | None = None


# ---------------------------------------------------------------------------
# Env helper — GC_LOOPS_ENABLED with XZ_CRON_ENABLED deprecated alias
# ---------------------------------------------------------------------------
def _read_loops_enabled_env() -> bool:
    """Resolve the new ``GC_LOOPS_ENABLED`` toggle with backward-compat alias.

    Resolution order (first match wins):

    1. ``GC_LOOPS_ENABLED`` explicitly set (even to ``false``) — use it.
    2. ``XZ_CRON_ENABLED`` deprecated alias — its value is used.
    3. Neither set — default ``true`` (loop pool starts; legacy cron
       coexists as a toggleable fallback).

    Truthy values: ``true`` / ``1`` / ``on`` / ``yes``.  Anything else
    (including empty string) is falsy.
    """
    raw_new = os.environ.get("GC_LOOPS_ENABLED")
    if raw_new is not None:
        return raw_new.strip().lower() not in ("false", "0", "off", "no", "n", "")
    raw_old = os.environ.get("XZ_CRON_ENABLED")
    if raw_old is not None:
        # Deprecation warning (logs once per process is enough; log always
        # here so ops see it during the transition window).
        logger.warning(
            "[scheduler] XZ_CRON_ENABLED is deprecated; "
            "use GC_LOOPS_ENABLED instead",
        )
        return raw_old.strip().lower() not in ("false", "0", "off", "no", "n", "")
    return True  # default ON


def start_scheduler() -> SchedulerBundle:
    """Construct + start all cron services + the NPC loop pool.

    Idempotent: a second call returns the existing bundle without
    re-starting the schedulers.

    Failures are logged and the partial bundle is returned.
    """
    global _bundle
    if _bundle is not None:
        return _bundle

    xiuzhen: XiuzhenCronService | None = None
    dm_followup: DmFollowupService | None = None
    npc_pool: NpcLoopPool | None = None
    behavior_coordinator: BehaviorCoordinator | None = None
    loops_enabled = _read_loops_enabled_env()

    try:
        xiuzhen = get_xiuzhen_cron_service()
        if loops_enabled:
            logger.info(
                "[scheduler] legacy xiuzhen cron kept dormant; "
                "behavior coordinator is authoritative",
            )
        else:
            xiuzhen.start()
    except Exception as exc:  # noqa: BLE001
        logger.exception("[scheduler] xiuzhen cron start failed: %s", exc)
        set_xiuzhen_cron_service(None)
        xiuzhen = None

    try:
        dm_followup = get_dm_followup_service()
        dm_followup.start()
    except Exception as exc:  # noqa: BLE001
        logger.exception("[scheduler] dm_followup start failed: %s", exc)
        set_dm_followup_service(None)
        dm_followup = None

    # MVP Candidate: one coordinator replaces six concurrently evaluating
    # loops. The legacy pool remains available for manual compatibility only.
    try:
        npc_pool = get_npc_loop_pool()
        if loops_enabled:
            behavior_coordinator = get_behavior_coordinator()
            # GC_LOOPS_ENABLED is authoritative. Constructing the dormant
            # legacy service may have copied XZ_CRON_ENABLED into shared state.
            behavior_coordinator.state.set_enabled(True)
            behavior_coordinator.start()
            logger.info("[scheduler] event-driven behavior coordinator started")
        else:
            logger.info(
                "[scheduler] NPC loop pool disabled via GC_LOOPS_ENABLED=false "
                "(legacy cron takes over)",
            )
    except Exception as exc:  # noqa: BLE001
        logger.exception("[scheduler] npc_loop pool init failed: %s", exc)
        set_npc_loop_pool(None)
        npc_pool = None

    # Build the bundle even if some services failed — admin endpoint can
    # still report partial state.
    if xiuzhen is None:
        xiuzhen = get_xiuzhen_cron_service()  # may re-create from env
    if dm_followup is None:
        dm_followup = get_dm_followup_service()
    if npc_pool is None:
        npc_pool = get_npc_loop_pool()
    if behavior_coordinator is None:
        behavior_coordinator = get_behavior_coordinator()

    _bundle = SchedulerBundle(
        xiuzhen=xiuzhen,
        dm_followup=dm_followup,
        npc_loop_pool=npc_pool,
        behavior_coordinator=behavior_coordinator,
    )
    logger.info(
        "[scheduler] started: xiuzhen=%s dm_followup=%s behavior=%s",
        xiuzhen.is_running(), dm_followup.is_running(), behavior_coordinator.is_running(),
    )
    return _bundle


async def shutdown_scheduler() -> None:
    """Stop all schedulers + the NPC loop pool.  Idempotent."""
    global _bundle
    if _bundle is None:
        return
    try:
        if _bundle.xiuzhen is not None:
            _bundle.xiuzhen.stop()
    except Exception as exc:  # noqa: BLE001
        logger.warning("[scheduler] xiuzhen stop failed: %s", exc)
    try:
        if _bundle.dm_followup is not None:
            _bundle.dm_followup.stop()
    except Exception as exc:  # noqa: BLE001
        logger.warning("[scheduler] dm_followup stop failed: %s", exc)
    try:
        if _bundle.behavior_coordinator is not None:
            await _bundle.behavior_coordinator.stop()
        await npc_loop_stop_all()
    except Exception as exc:  # noqa: BLE001
        logger.warning("[scheduler] npc_loop stop failed: %s", exc)
    _bundle = None
    logger.info("[scheduler] shutdown complete")


def get_scheduler() -> SchedulerBundle | None:
    """Return the current scheduler bundle (or None if not started)."""
    return _bundle


def set_scheduler(bundle: SchedulerBundle | None) -> SchedulerBundle | None:
    """Replace (or clear) the singleton bundle.  Used by tests."""
    global _bundle
    old = _bundle
    _bundle = bundle
    return old


def status_dict() -> dict[str, Any]:
    """Return a JSON-friendly status snapshot for /api/cron/status."""
    if _bundle is None:
        return {
            "started": False,
            "xiuzhen": None,
            "dm_followup": None,
            "npc_loop_pool": None,
            "behavior_coordinator": None,
        }
    return {
        "started": True,
        "xiuzhen": _bundle.xiuzhen.status_dict(),
        "dm_followup": _bundle.dm_followup.status_dict(),
        "npc_loop_pool": _bundle.npc_loop_pool.status_dict(),
        "behavior_coordinator": _bundle.behavior_coordinator.status_dict(),
    }


__all__ = [
    "SchedulerBundle",
    "start_scheduler",
    "shutdown_scheduler",
    "get_scheduler",
    "set_scheduler",
    "status_dict",
]
