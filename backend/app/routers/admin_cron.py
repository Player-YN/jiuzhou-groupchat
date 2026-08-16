"""Admin endpoints for the cron scheduler.

Routes:

- ``GET  /api/cron/status``  — full status snapshot (enabled, intervals,
  fire counts, next-fire time, last error).
- ``POST /api/cron/toggle`` — toggle ``enabled`` and/or set ``npc_filter``
  without a process restart.  Body:

      {
        "enabled": bool | None,             # None = leave as-is
        "npc_filter": list[str] | None,    # None = clear filter (allow all 6)
        "interval_min": int | None,         # None = leave as-is
        "interval_hour": float | None,      # DM followup interval
        "idle_hour": float | None           # DM followup idle threshold
      }

  Returns the new status snapshot.
- ``POST /api/cron/trigger`` — force a fire (for manual testing).  Body:

      {"service": "xiuzhen" | "dm_followup"}

  Returns the fire-once summary dict.
"""
from __future__ import annotations

import logging
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.letta_bridge.agent_manager import ROLE_AGENT_KEYS
from app.scheduler import (
    get_scheduler,
    start_scheduler,
    status_dict,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/cron", tags=["cron"])


# ============================================================================
# Request models
# ============================================================================


class ToggleRequest(BaseModel):
    """POST /api/cron/toggle body."""

    enabled: bool | None = Field(
        default=None,
        description="Set True/False to enable/disable cron; None leaves unchanged.",
    )
    npc_filter: list[str] | None = Field(
        default=None,
        description="Whitelist of role_keys (6 九洲一号群 keys); None clears (allow all).",
    )
    interval_min: int | None = Field(
        default=None,
        ge=1, le=1440,
        description="Group cron interval in minutes (clamped 1..1440).",
    )
    interval_hour: float | None = Field(
        default=None,
        ge=0.016, le=168.0,
        description="DM followup interval in hours.",
    )
    idle_hour: float | None = Field(
        default=None,
        ge=0.016, le=720.0,
        description="DM followup idle threshold in hours.",
    )


class TriggerRequest(BaseModel):
    """POST /api/cron/trigger body."""

    service: Literal["xiuzhen", "dm_followup", "npc_loop", "behavior"] = "xiuzhen"
    # When service == "npc_loop", target selects which NPC's autonomous loop
    # to fire once.  Default: None = pick first NPC in pool.
    target: str | None = Field(
        default=None,
        description=(
            "For npc_loop: role to trigger. For behavior promise/relationship events: "
            "the role primarily responsible for the event."
        ),
    )
    behavior_event_type: Literal[
        "npc_message", "relationship_change", "promise_due", "world_event", "idle_tick"
    ] = "idle_tick"
    text: str | None = Field(
        default=None,
        description="Stimulus text for service='behavior'.",
    )
    chain_depth: int = Field(default=0, ge=0, le=3)


# ============================================================================
# Helpers
# ============================================================================


def _validate_npc_filter(npc_filter: list[str] | None) -> list[str] | None:
    """Drop unknown role_keys silently; return None if all invalid."""
    if npc_filter is None:
        return None
    valid = [n for n in npc_filter if n in ROLE_AGENT_KEYS]
    return valid if valid else None


# ============================================================================
# Routes
# ============================================================================


@router.get("/status")
async def get_cron_status() -> dict[str, Any]:
    """Return the cron scheduler status snapshot.

    Always returns HTTP 200.  The body shape depends on whether the
    scheduler has been started yet (returns ``{"started": False, ...}`` if
    not).

    Example response:

        {
          "started": true,
          "xiuzhen": {
            "running": true, "interval_min": 5, "enabled": true,
            "npc_filter": null, "next_fire_time": "2026-07-04T20:15:00+00:00",
            "fire_count": 3, "last_error": null
          },
          "dm_followup": {
            "running": true, "interval_hour": 1.0, "idle_hour": 24.0,
            "next_fire_time": null, "fire_count": 0, "last_error": null
          }
        }
    """
    bundle = get_scheduler()
    if bundle is None:
        return {
            "started": False,
            "xiuzhen": None,
            "dm_followup": None,
        }
    return status_dict()


@router.post("/toggle")
async def toggle_cron(req: ToggleRequest) -> dict[str, Any]:
    """Enable / disable the cron and/or adjust interval + npc_filter.

    Lazy-starts the scheduler if it wasn't running yet (the toggle is a
    useful single-shot "wake up" command for ops).
    """
    # 1) Make sure the scheduler is up
    bundle = get_scheduler()
    if bundle is None:
        bundle = start_scheduler()
        if bundle is None:  # start_scheduler should always return a bundle
            raise HTTPException(status_code=503, detail="scheduler failed to start")

    # 2) Apply toggles
    if req.enabled is not None:
        bundle.xiuzhen.set_enabled(bool(req.enabled))
        # Note: we share the same CronState.enabled with dm_followup
        # (they look at the same state.enabled flag).
        bundle.dm_followup.state.set_enabled(bool(req.enabled))

    if req.npc_filter is not None:
        bundle.xiuzhen.set_npc_filter(_validate_npc_filter(req.npc_filter))

    if req.interval_min is not None:
        bundle.xiuzhen.set_interval_min(int(req.interval_min))

    if req.interval_hour is not None:
        bundle.dm_followup.set_interval_hour(float(req.interval_hour))

    if req.idle_hour is not None:
        bundle.dm_followup.set_idle_hour(float(req.idle_hour))

    return status_dict()


@router.post("/trigger")
async def trigger_cron(req: TriggerRequest) -> dict[str, Any]:
    """Force a one-shot fire of the named service.

    Returns the fire-once summary dict (same shape the scheduled job would
    have logged).

    Useful for ops / smoke tests.
    """
    bundle = get_scheduler()
    if bundle is None:
        bundle = start_scheduler()
        if bundle is None:
            raise HTTPException(status_code=503, detail="scheduler failed to start")

    if req.service == "xiuzhen":
        return await bundle.xiuzhen.trigger_now()
    if req.service == "dm_followup":
        return await bundle.dm_followup.trigger_now()
    if req.service == "npc_loop":
        # ADR-0007 Option B — trigger ONE autonomous cycle on one NPC.
        # We pick target from the pool, or fall back to the first NPC.
        target = req.target
        if target is None:
            loops = bundle.npc_loop_pool.loops()
            target = next(iter(loops.keys()))
        if target not in bundle.npc_loop_pool.loops():
            raise HTTPException(
                status_code=400,
                detail=f"unknown npc_loop target: {target!r}",
            )
        return await bundle.npc_loop_pool.trigger_one(target)
    if req.service == "behavior":
        return await bundle.behavior_coordinator.trigger(
            req.behavior_event_type,
            text=req.text or "管理员触发一次群聊行为评估。",
            speaker_key=req.target or "system",
            chain_depth=req.chain_depth,
            force=True,
        )
    raise HTTPException(status_code=400, detail=f"unknown service: {req.service!r}")


__all__ = ["router"]
