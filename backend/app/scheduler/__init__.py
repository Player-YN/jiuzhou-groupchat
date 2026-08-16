"""Cron-based proactive behaviour scheduler (Stage 8 Cron / Stage 8-NPC-Love).

Background jobs that fire even when no user is talking:

- ``xiuzhen_cron`` (legacy) — random group poster retained as a fallback only.
  It stays dormant whenever the event-driven coordinator is enabled, so two
  proactive policies can never speak concurrently.

- ``npc_loop_pool`` (Stage 8-NPC-Love, ADR-0007 Option B) — 6 autonomous
  loops, one per NPC, each reading recent group context and deciding
  for itself whether to speak.  This is the default proactive group
  posting mechanism when ``GC_LOOPS_ENABLED`` is true.

- ``dm_followup`` — every hour, scan ``AgentMemoryStore`` for ``(session_id,
  agent_key)`` pairs that have been idle > 24h and let the agent proactively
  follow up via the DM pipeline.

All services are wrapped by ``APScheduler``'s ``AsyncIOScheduler`` (or
``asyncio.create_task`` for the NPC loop pool) and managed from the FastAPI
lifespan hook.  Failures are **always** caught inside the job body so the
scheduler itself never crashes.

Public surface:

- ``XiuzhenCronService``       — legacy group proactive service
- ``DmFollowupService``        — DM proactive service
- ``NpcLoopPool`` / ``NpcLoop`` — per-NPC autonomous loops
- ``GroupChatSemaphore``        — group push single-flight + cool-down
- ``stream_via_letta_with_retry`` — retry wrapper around ``_stream_via_letta``
- ``ConnectionRegistry``        — active WebSocket session registry
- ``get_cron_status`` / ``toggle_cron``  — admin helpers used by
  ``app/routers/admin_cron.py``
- ``start_scheduler`` / ``stop_scheduler``  — lifespan hooks
"""
from __future__ import annotations

from app.scheduler.connection_registry import (
    ConnectionRegistry,
    get_connection_registry,
    set_connection_registry,
)
from app.scheduler.behavior_coordinator import (
    BehaviorCoordinator,
    get_behavior_coordinator,
    set_behavior_coordinator,
)
from app.scheduler.group_semaphore import (
    GroupChatSemaphore,
    get_group_semaphore,
    set_group_semaphore,
)
from app.scheduler.letta_retry import (
    LettaRetryExhausted,
    stream_via_letta_with_retry,
)
from app.scheduler.npc_loop import (
    NpcLoop,
    NpcLoopPool,
    get_npc_loop_pool,
    set_npc_loop_pool,
    start_all as npc_loop_start_all,
    stop_all as npc_loop_stop_all,
)
from app.scheduler.state import (
    CronState,
    get_cron_state,
    set_cron_state,
)
from app.scheduler.xiuzhen_cron import XiuzhenCronService
from app.scheduler.dm_followup import DmFollowupService
from app.scheduler.lifespan import (
    get_scheduler,
    set_scheduler,
    shutdown_scheduler,
    start_scheduler,
    status_dict,
)


__all__ = [
    # connection registry
    "ConnectionRegistry",
    "get_connection_registry",
    "set_connection_registry",
    "BehaviorCoordinator",
    "get_behavior_coordinator",
    "set_behavior_coordinator",
    # shared state
    "CronState",
    "get_cron_state",
    "set_cron_state",
    # legacy services
    "XiuzhenCronService",
    "DmFollowupService",
    # Stage 8-NPC-Love (ADR-0007 Option B)
    "NpcLoop",
    "NpcLoopPool",
    "get_npc_loop_pool",
    "set_npc_loop_pool",
    "npc_loop_start_all",
    "npc_loop_stop_all",
    "GroupChatSemaphore",
    "get_group_semaphore",
    "set_group_semaphore",
    "LettaRetryExhausted",
    "stream_via_letta_with_retry",
    # lifespan hooks
    "start_scheduler",
    "shutdown_scheduler",
    "get_scheduler",
    "set_scheduler",
    "status_dict",
]
