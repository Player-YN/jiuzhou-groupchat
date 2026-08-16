"""Per-NPC Letta agent registry — Stage 7.

Architecture (per `AGENTS.md` Stage 7 spec):
  - One Letta server (port 8283) hosts 6 NPCs.
  - Each NPC (shu-hang / yao-shi / san-lang / bei-he / bai-qianbei /
    ling-die) gets ONE persistent Letta agent.
  - `role_key` -> `agent_id` mapping is persisted in a SQLite table
    `letta_npc_registry` so the agent survives container restarts (the
    Letta server's own state is on disk-backed Postgres; the registry
    is just a quick lookup table on our side).
  - Bootstrap is idempotent: on startup we list Letta agents and either
    reuse the existing one (verified via GET /v1/agents/{id}) or create
    a fresh one + seed its persona block + archival memory.

This module follows Project A's `bff/letta_bridge/agent_manager.py`
pattern with two adaptations:
  1. The registry is **SQLite-backed**, not Postgres-backed.  Reason:
     Project B is still in MVP — keeping the registry in the same SQLite
     as `AgentMemoryStore` avoids spinning up a separate database.  The
     schema is intentionally small so a future Postgres port is one
     DDL change away.
  2. We bootstrap ALL 6 NPCs eagerly on BFF startup (Project A uses a
     lazy per-NPC bootstrap on first chat turn).  Eager bootstrap is
     fine here because the corpus is bounded and the agents are tiny.

Public API:
    `LettaAgentRegistry(db_path)`           — sync registry over SQLite
    `bootstrap_all(client, llm_model)`      — idempotent bootstrap, returns
                                              `BootstrapOutcome`
    `get_agent_id(role_key)`                — cached lookup
    `get_default_registry(...)`             — process-wide singleton
"""
from __future__ import annotations

import logging
import sqlite3
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.letta_bridge.letta_client import LettaClient, LettaError
from app.letta_bridge.role_seeds import (
    ROLE_AGENT_KEYS,
    agent_name_for,
    build_archival_seed_entries,
    build_npc_memory_blocks,
)

logger = logging.getLogger(__name__)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS letta_npc_registry (
    role_key       TEXT PRIMARY KEY,
    agent_id       TEXT NOT NULL,
    agent_name     TEXT NOT NULL,
    llm_model      TEXT NOT NULL,
    created_at     INTEGER NOT NULL
);
"""


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------


@dataclass
class BootstrapOutcome:
    """Per-NPC outcomes from a `bootstrap_all` call.

    Returned so callers (BFF lifespan, tests) can inspect what happened
    without parsing log strings.
    """

    created: list[str] = field(default_factory=list)
    reused: list[str] = field(default_factory=list)
    recovered: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)


class RegistryError(RuntimeError):
    """Raised for unrecoverable registry failures."""


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class LettaAgentRegistry:
    """SQLite-backed `role_key -> agent_id` registry.

    Thread-safe via `threading.Lock`.  One instance per process is the
    typical pattern (see `get_default_registry`), but tests can construct
    ephemeral instances directly with `:memory:` SQLite.
    """

    def __init__(self, db_path: str | Path):
        self._db_path = str(db_path)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        # Process-level cache; rebuilt by `bootstrap_all` after each call.
        self._cache: dict[str, str] = {}
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ------------------------------------------------------------------
    # reads
    # ------------------------------------------------------------------
    def get_agent_id(self, role_key: str) -> str | None:
        """Return the cached `agent_id` for `role_key`, or None."""
        cached = self._cache.get(role_key)
        if cached:
            return cached
        with self._lock:
            row = self._conn.execute(
                "SELECT agent_id FROM letta_npc_registry WHERE role_key = ?",
                (role_key,),
            ).fetchone()
        if row is None:
            return None
        aid = row["agent_id"]
        self._cache[role_key] = aid
        return aid

    def list_all(self) -> list[dict[str, Any]]:
        """Return all rows (debug / admin)."""
        with self._lock:
            cur = self._conn.execute(
                """
                SELECT role_key, agent_id, agent_name, llm_model, created_at
                  FROM letta_npc_registry
                 ORDER BY role_key
                """
            )
            return [
                {
                    "role_key": r["role_key"],
                    "agent_id": r["agent_id"],
                    "agent_name": r["agent_name"],
                    "llm_model": r["llm_model"],
                    "created_at": r["created_at"],
                }
                for r in cur.fetchall()
            ]

    # ------------------------------------------------------------------
    # bootstrap
    # ------------------------------------------------------------------
    async def bootstrap_all(
        self,
        client: LettaClient,
        llm_model: str,
    ) -> BootstrapOutcome:
        """Idempotently create or reuse one Letta agent per NPC role.

        Args:
            client: authenticated Letta HTTP client.
            llm_model: Letta-format model handle (e.g.
                `openai/gpt-4o-mini` or `local-ollama/qwen2.5:1.5b`).
                Used only for **new** agent creation; existing agents
                are NOT migrated.

        Returns:
            `BootstrapOutcome` enumerating per-NPC outcomes.

        Behaviour:
            - For each canonical role:
                - If row exists AND Letta agent exists → reused
                - If row exists but Letta agent 404 → recovered (rebuild + update)
                - If no row → created (insert + seed)
        """
        outcome = BootstrapOutcome()

        for role_key in ROLE_AGENT_KEYS:
            try:
                existing_agent_id = self.get_agent_id(role_key)
                if existing_agent_id:
                    # Verify the persisted Letta agent still exists.
                    if await _verify_agent_alive(client, existing_agent_id):
                        outcome.reused.append(role_key)
                        continue
                    logger.warning(
                        "registry row for %s has stale agent_id=%s; re-creating",
                        role_key, existing_agent_id,
                    )

                # Create or rebuild.
                agent_id = await _create_npc_agent(
                    client, role_key, llm_model, seed_archival=True,
                )
                # Upsert registry row.
                self._upsert_row(role_key, agent_id, llm_model)
                self._cache[role_key] = agent_id
                if existing_agent_id:
                    outcome.recovered.append(role_key)
                else:
                    outcome.created.append(role_key)
            except Exception as exc:  # noqa: BLE001 — keep bootstrap going
                logger.exception("bootstrap failed for npc %s", role_key)
                outcome.failed.append((role_key, f"{type(exc).__name__}: {exc}"))

        logger.info(
            "letta_agent_registry bootstrap: created=%s reused=%s recovered=%s failed=%s",
            outcome.created, outcome.reused, outcome.recovered, outcome.failed,
        )
        return outcome

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------
    def _upsert_row(self, role_key: str, agent_id: str, llm_model: str) -> None:
        import time
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO letta_npc_registry
                    (role_key, agent_id, agent_name, llm_model, created_at)
                     VALUES (?, ?, ?, ?, ?)
                ON CONFLICT (role_key) DO UPDATE
                       SET agent_id = EXCLUDED.agent_id,
                           agent_name = EXCLUDED.agent_name,
                           llm_model = EXCLUDED.llm_model,
                           created_at = EXCLUDED.created_at
                """,
                (
                    role_key, agent_id, agent_name_for(role_key),
                    llm_model, int(time.time() * 1000),
                ),
            )
            self._conn.commit()


# ---------------------------------------------------------------------------
# Standalone helpers — used by both bootstrap and direct callers
# ---------------------------------------------------------------------------


async def _verify_agent_alive(client: LettaClient, agent_id: str) -> bool:
    """Return True iff GET /v1/agents/{id} returns 200."""
    try:
        await client.get_agent(agent_id)
        return True
    except LettaError as exc:
        msg = str(exc)
        if "404" in msg:
            return False
        # Treat any other error as "alive" (transient Letta outage should
        # NOT kill our registry rows).
        logger.warning("verify_agent_alive for %s returned %s; assuming alive", agent_id, exc)
        return True


async def _create_npc_agent(
    client: LettaClient,
    role_key: str,
    llm_model: str,
    seed_archival: bool = True,
) -> str:
    """Create a fresh NPC agent + (optionally) seed archival memory.

    Returns:
        the new `agent_id`.

    Raises:
        LettaError / RegistryError on any Letta I/O failure.
    """
    payload = {
        "name": agent_name_for(role_key),
        "model": llm_model,
        "memory_blocks": build_npc_memory_blocks(role_key),
    }
    created = await client.create_agent(payload)
    agent_id = created.get("id") or created.get("agent_id")
    if not agent_id:
        raise RegistryError(f"create_agent returned no id: {created!r}")
    logger.info("created npc agent %s (id=%s)", agent_name_for(role_key), agent_id)

    if seed_archival:
        for entry in build_archival_seed_entries(role_key):
            try:
                await client.insert_archival_memory(agent_id, entry)
            except LettaError as exc:
                # Don't fail agent creation just because archival seeding
                # failed — the agent itself is still useful.
                logger.warning(
                    "archival seed insert failed for %s/%s: %s",
                    role_key, agent_id, exc,
                )
    return agent_id


# ---------------------------------------------------------------------------
# Process-wide singleton (mirrors AgentMemoryStore pattern)
# ---------------------------------------------------------------------------

_default_registry: LettaAgentRegistry | None = None
_default_lock = threading.Lock()


def _default_db_path() -> str:
    """Default registry SQLite path: `backend/data/letta_npc_registry.sqlite`.

    Override via `LETTA_REGISTRY_DB_PATH` env var.
    """
    import os
    env = os.environ.get("LETTA_REGISTRY_DB_PATH")
    if env:
        return env
    backend_root = Path(__file__).resolve().parents[2]
    data_dir = backend_root / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    return str(data_dir / "letta_npc_registry.sqlite")


def get_default_registry() -> LettaAgentRegistry:
    """Return the process-wide singleton LettaAgentRegistry."""
    global _default_registry
    with _default_lock:
        if _default_registry is None:
            _default_registry = LettaAgentRegistry(_default_db_path())
        return _default_registry


def set_default_registry(reg: LettaAgentRegistry | None) -> LettaAgentRegistry | None:
    """Replace (or clear) the process-wide singleton.  Returns the old one."""
    global _default_registry
    with _default_lock:
        old = _default_registry
        _default_registry = reg
        return old


# Convenience alias used by graph.py: callers that already have a
# registry reference shouldn't have to import the symbols above.
async def get_or_create_agent_id(
    client: LettaClient,
    role_key: str,
    llm_model: str,
    *,
    registry: LettaAgentRegistry | None = None,
) -> str:
    """Return the agent_id for `role_key`, creating + persisting on miss.

    This is the hot-path helper used by `make_agent_node` and
    `stream_dm_chat` — it MUST be cheap on the warm path (registry hit
    → single dict lookup).
    """
    reg = registry or get_default_registry()
    agent_id = reg.get_agent_id(role_key)
    if agent_id and await _verify_agent_alive(client, agent_id):
        return agent_id
    # Cache miss / stale → create fresh.
    new_id = await _create_npc_agent(client, role_key, llm_model, seed_archival=True)
    reg._upsert_row(role_key, new_id, llm_model)  # noqa: SLF001 — internal but OK
    reg._cache[role_key] = new_id  # noqa: SLF001
    return new_id


__all__ = [
    "BootstrapOutcome",
    "LettaAgentRegistry",
    "RegistryError",
    "ROLE_AGENT_KEYS",
    "get_default_registry",
    "get_or_create_agent_id",
    "set_default_registry",
]