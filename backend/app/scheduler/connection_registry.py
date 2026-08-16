"""In-memory registry of active WebSocket sessions.

Used by the cron proactive services to push events to currently connected
clients.  The registry stores ``session_id -> WebSocket`` and is process-wide
(thread-safe via a single lock).  It survives FastAPI restarts because each
process builds its own — a restart resets the registry to empty, which is
the correct behaviour for an in-memory socket cache.

Public surface:

- ``ConnectionRegistry``           — the registry class
- ``get_connection_registry()``    — process-wide singleton accessor
- ``set_connection_registry(reg)`` — test/replace helper
"""
from __future__ import annotations

import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import WebSocket


class ConnectionRegistry:
    """Process-wide ``session_id -> WebSocket`` registry.

    WebSocket connections are added on WS accept and removed on disconnect.
    Concurrent access is serialised by an internal lock — websocket objects
    are bound to the asyncio loop that owns them, so we never block on them
    while holding the lock (send/receive happens in coroutines that release
    the lock first).
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._connections: dict[str, "WebSocket"] = {}

    # ------------------------------------------------------------------
    # mutations
    # ------------------------------------------------------------------
    def register(self, session_id: str, websocket: "WebSocket") -> None:
        """Add a websocket to the registry.  Last-write-wins on duplicate sid."""
        with self._lock:
            self._connections[session_id] = websocket

    def unregister(self, session_id: str) -> None:
        """Remove a websocket by session_id.  Missing keys are silent no-ops."""
        with self._lock:
            self._connections.pop(session_id, None)

    def clear(self) -> None:
        """Drop all registered connections (test teardown)."""
        with self._lock:
            self._connections.clear()

    # ------------------------------------------------------------------
    # reads
    # ------------------------------------------------------------------
    def get(self, session_id: str) -> "WebSocket | None":
        """Return the websocket for ``session_id`` or ``None`` if absent."""
        with self._lock:
            return self._connections.get(session_id)

    def active_sessions(self) -> list[str]:
        """Return a snapshot of currently registered session_ids."""
        with self._lock:
            return list(self._connections.keys())

    def active_count(self) -> int:
        """Return the number of active connections (cheap)."""
        with self._lock:
            return len(self._connections)

    def __len__(self) -> int:
        return self.active_count()

    def __contains__(self, session_id: object) -> bool:
        with self._lock:
            return isinstance(session_id, str) and session_id in self._connections


# ============================================================================
# Module-level singleton
# ============================================================================
_registry: ConnectionRegistry | None = None
_registry_lock = threading.Lock()


def get_connection_registry() -> ConnectionRegistry:
    """Return the process-wide ``ConnectionRegistry`` (lazy init)."""
    global _registry
    with _registry_lock:
        if _registry is None:
            _registry = ConnectionRegistry()
        return _registry


def set_connection_registry(reg: ConnectionRegistry | None) -> ConnectionRegistry | None:
    """Replace (or clear) the singleton.  Returns the previous instance.

    Tests pass ``ConnectionRegistry()`` to get a fresh, isolated registry;
    production code does NOT call this.
    """
    global _registry
    with _registry_lock:
        old = _registry
        _registry = reg
        return old