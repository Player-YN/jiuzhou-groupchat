"""Letta v0.16.8 bridge package.

Provides:
    - `LettaClient` — async HTTP wrapper around the Letta REST API
      (`app/letta_bridge/letta_client.py`).
    - `LettaAgentRegistry` + `get_or_create_agent_id` — persistent
      `role_key → agent_id` mapping backed by SQLite, with idempotent
      bootstrap that seeds persona core-memory + archival passages
      (`app/letta_bridge/agent_manager.py`).
    - `role_seeds` — pure helpers that translate Project B's `ROLES`
      dict into Letta memory-block payloads + archival seed passages
      (`app/letta_bridge/role_seeds.py`).
    - `singleton` — process-wide LettaClient singleton + helper for
      tests (`app/letta_bridge/singleton.py`).

The bridge is consumed by:
    - `app/graph.py` — replaces per-role `minimax` / `agnes` LLM calls
      with `LettaClient.stream_message(agent_id, ...)` for both group
      chat (LangGraph Supervisor leaves) and DM (single-agent stream).
    - `app/main.py` lifespan — calls `bootstrap_all` on BFF startup.
"""
from app.letta_bridge.agent_manager import (
    BootstrapOutcome,
    LettaAgentRegistry,
    RegistryError,
    ROLE_AGENT_KEYS,
    get_default_registry,
    get_or_create_agent_id,
    set_default_registry,
)
from app.letta_bridge.letta_client import LettaClient, LettaError
from app.letta_bridge.role_seeds import (
    agent_name_for,
    build_archival_seed_entries,
    build_npc_memory_blocks,
)
from app.letta_bridge.singleton import (
    aclose_letta_client,
    get_letta_client,
    set_letta_client_for_tests,
)

__all__ = [
    "BootstrapOutcome",
    "LettaAgentRegistry",
    "LettaClient",
    "LettaError",
    "RegistryError",
    "ROLE_AGENT_KEYS",
    "aclose_letta_client",
    "agent_name_for",
    "build_archival_seed_entries",
    "build_npc_memory_blocks",
    "get_default_registry",
    "get_letta_client",
    "get_or_create_agent_id",
    "set_default_registry",
    "set_letta_client_for_tests",
]