"""DM (direct message) + Agent memory package.

Stage 7 Bug 2 增强: 新增 AgentMemoryStore (per-agent 统一 source-aware memory)。

Public API：
    - `DmStore`: SQLite-backed per-agent DM history store (Stage 6,legacy)
    - `get_dm_store` / `set_dm_store` / `close_dm_store`: DmStore 单例管理
    - `AgentMemoryStore`: SQLite-backed per-agent 统一 source-aware memory (Stage 7)
    - `get_agent_memory_store` / `set_agent_memory_store` / `close_agent_memory_store`:
      AgentMemoryStore 单例管理

详细实现见 `dm_store.py` + `agent_memory.py`。
"""
from __future__ import annotations

from app.memory.agent_memory import (
    AgentMemoryStore,
    ROLE_AGENT_KEYS,
    close_agent_memory_store,
    get_agent_memory_store,
    set_agent_memory_store,
)
from app.memory.dm_store import (
    DmStore,
    close_dm_store,
    get_dm_store,
    set_dm_store,
)

__all__ = [
    # DmStore (Stage 6 legacy)
    "DmStore",
    "get_dm_store",
    "set_dm_store",
    "close_dm_store",
    # AgentMemoryStore (Stage 7 Bug 2)
    "AgentMemoryStore",
    "ROLE_AGENT_KEYS",
    "get_agent_memory_store",
    "set_agent_memory_store",
    "close_agent_memory_store",
]