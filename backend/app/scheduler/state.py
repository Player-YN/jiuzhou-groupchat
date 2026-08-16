"""Shared state for the cron services.

A single ``CronState`` dataclass tracks:

- ``enabled``                 — global on/off switch (mirrors ``XZ_CRON_ENABLED``)
- ``npc_filter``              — optional whitelist; ``None`` = all 6 NPCs
- ``group_fire_count``        — how many times the group cron has fired
- ``dm_fire_count``            — how many times the DM followup has fired
- ``last_fire_at: dict[str, float]``
                              — per-NPC last-fire timestamp (epoch seconds)
                                used for the 1h throttling rule
- ``last_dm_followup_at: dict[tuple[str, str], float]``
                              — per ``(session_id, agent_key)`` last-fire
                                timestamp; same 1h throttle applies
- ``last_error``              — most recent error string (admin display)

The state is process-wide and protected by a single lock; it does NOT
persist across process restarts (acceptable because the throttling rule
is just a courtesy and not a strict SLA).

Public surface:

- ``CronState``
- ``get_cron_state()``
- ``set_cron_state(state)``
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field


@dataclass
class CronState:
    """Mutable shared state for both cron services.

    Mutating methods take the internal lock so concurrent job bodies can't
    corrupt the dicts / counters.
    """

    enabled: bool = True
    npc_filter: list[str] | None = None        # None = all 6 NPCs
    group_fire_count: int = 0
    dm_fire_count: int = 0
    last_fire_at: dict[str, float] = field(default_factory=dict)        # role_key -> epoch
    last_dm_followup_at: dict[tuple[str, str], float] = field(
        default_factory=dict,
    )                                                                    # (sid, role) -> epoch
    last_error: str | None = None
    last_group_fire_at: float | None = None
    last_dm_fire_at: float | None = None

    # ------------------------------------------------------------------
    # mutations (lock-protected)
    # ------------------------------------------------------------------
    def record_group_fire(self, role_key: str, *, ok: bool = True, error: str | None = None) -> None:
        """Increment the counter and stamp the per-NPC last-fire timestamp.

        Args:
            role_key: 九洲一号群 6 角色之一
            ok: True if the fire succeeded; False to stamp the error
            error: error string when ``ok=False`` (overwrites ``last_error``)
        """
        now = time.time()
        self.group_fire_count += 1
        self.last_group_fire_at = now
        if ok:
            self.last_fire_at[role_key] = now
            self.last_error = None
        else:
            self.last_error = error

    def record_dm_fire(self, session_id: str, role_key: str, *, ok: bool = True, error: str | None = None) -> None:
        """Increment the DM counter and stamp the (sid, role) last-fire timestamp."""
        now = time.time()
        self.dm_fire_count += 1
        self.last_dm_fire_at = now
        if ok:
            self.last_dm_followup_at[(session_id, role_key)] = now
            self.last_error = None
        else:
            self.last_error = error

    def should_throttle(self, role_key: str, min_interval_sec: float, *, now: float | None = None) -> bool:
        """Return True iff this NPC has fired within ``min_interval_sec`` seconds.

        Used by ``XiuzhenCronService`` to enforce the 1h same-NPC throttle.
        """
        if now is None:
            now = time.time()
        last = self.last_fire_at.get(role_key)
        if last is None:
            return False
        return (now - last) < min_interval_sec

    def should_throttle_dm(self, session_id: str, role_key: str, min_interval_sec: float, *, now: float | None = None) -> bool:
        """Return True iff this ``(sid, role)`` has fired DM followup within ``min_interval_sec``."""
        if now is None:
            now = time.time()
        last = self.last_dm_followup_at.get((session_id, role_key))
        if last is None:
            return False
        return (now - last) < min_interval_sec

    def set_enabled(self, enabled: bool) -> None:
        self.enabled = bool(enabled)

    def set_npc_filter(self, npc_filter: list[str] | None) -> None:
        """Set / clear the optional NPC whitelist.

        ``None`` clears the filter (all 6 NPCs allowed).  An empty list
        disables cron entirely (defensive — admin endpoint normalises).
        """
        if npc_filter is None:
            self.npc_filter = None
            return
        # Defensive copy + filter to known roles only.
        self.npc_filter = list(npc_filter)

    def snapshot(self) -> dict:
        """Return a JSON-friendly view (used by ``/api/cron/status``)."""
        return {
            "enabled": self.enabled,
            "npc_filter": list(self.npc_filter) if self.npc_filter else None,
            "group_fire_count": self.group_fire_count,
            "dm_fire_count": self.dm_fire_count,
            "last_fire_at": dict(self.last_fire_at),
            "last_dm_followup_at": {
                f"{sid}|{role}": ts for (sid, role), ts in self.last_dm_followup_at.items()
            },
            "last_group_fire_at": self.last_group_fire_at,
            "last_dm_fire_at": self.last_dm_fire_at,
            "last_error": self.last_error,
        }


# ============================================================================
# Module-level singleton (the state itself is the shared resource)
# ============================================================================
_state: CronState | None = None
_state_lock = threading.Lock()


def get_cron_state() -> CronState:
    """Return the process-wide ``CronState`` (lazy init)."""
    global _state
    with _state_lock:
        if _state is None:
            _state = CronState()
        return _state


def set_cron_state(state: CronState | None) -> CronState | None:
    """Replace (or clear) the singleton.  Returns the previous instance.

    Tests use this to inject isolated state; production code MUST NOT
    call this.
    """
    global _state
    with _state_lock:
        old = _state
        _state = state
        return old


def reset_cron_state_for_tests() -> None:
    """Test helper: drop the singleton so the next ``get_cron_state()`` returns a fresh instance."""
    set_cron_state(None)