"""Per-agent DM (direct message) memory store.

每个 (session_id, agent_key) 对应一份独立的私信历史，与群聊完全隔离。
这是 Stage 6 DM Phase 2 的持久化层。

设计要点：
- SQLite 单表 `dm_history`（append-only），按 (session_id, agent_key) 索引
- API：load / append / count / list_sessions / clear
- 隐私保证：load 只返回指定 (session_id, agent_key) 的消息，**绝不跨 agent_key 返回**
- 测试可注入 `:memory:` 或临时 SQLite 文件；生产走 `backend/data/dm_history.sqlite`

后端是 source of truth：协议定义在 `app/models.py`（DmMessage / DmInitResponsePayload）。
"""
from __future__ import annotations

import os
import sqlite3
import threading
import time
from pathlib import Path

from app.models import DmMessage


# ============================================================================
# Schema
# ============================================================================
_SCHEMA = """
CREATE TABLE IF NOT EXISTS dm_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT    NOT NULL,
    agent_key   TEXT    NOT NULL,
    role        TEXT    NOT NULL CHECK(role IN ('user', 'agent')),
    text        TEXT    NOT NULL,
    timestamp   INTEGER NOT NULL,
    agent_name  TEXT,
    agent_emoji TEXT
);
CREATE INDEX IF NOT EXISTS idx_dm_session_agent
    ON dm_history(session_id, agent_key, id);
"""


# ============================================================================
# DmStore
# ============================================================================
class DmStore:
    """SQLite-backed per-agent DM memory store.

    单实例可服务多 session / 多 agent；线程安全（通过 `threading.Lock` 串行化）。
    """

    def __init__(self, db_path: str | Path):
        self._db_path = str(db_path)
        self._lock = threading.Lock()
        # 允许并发读：check_same_thread=False，但 sqlite3 默认串行化是 OK 的
        # （写路径用 lock 保护）
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self.init_schema()

    # ----- 生命周期 -----
    def init_schema(self) -> None:
        """创建 dm_history 表 + 索引。幂等。"""
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ----- CRUD -----
    def load_history(self, session_id: str, agent_key: str) -> list[DmMessage]:
        """读取某个 (session_id, agent_key) 的全部历史，按时间升序。

        **隐私保证**：只返回指定 agent_key 的消息；绝不跨 agent_key 泄漏。
        即使调用方传错 agent_key，也只会返回空列表（SQL 严格按 WHERE 过滤）。
        """
        if not session_id or not agent_key:
            return []
        with self._lock:
            cur = self._conn.execute(
                """
                SELECT role, text, timestamp, agent_name, agent_emoji
                FROM dm_history
                WHERE session_id = ? AND agent_key = ?
                ORDER BY id ASC
                """,
                (session_id, agent_key),
            )
            rows = cur.fetchall()
        return [
            DmMessage(
                role=r["role"],
                text=r["text"],
                timestamp=r["timestamp"],
                agent_key=agent_key,
                agent_name=r["agent_name"],
                agent_emoji=r["agent_emoji"],
            )
            for r in rows
        ]

    def append_message(
        self,
        session_id: str,
        agent_key: str,
        role: str,
        text: str,
        timestamp: int | None = None,
        agent_name: str | None = None,
        agent_emoji: str | None = None,
    ) -> DmMessage:
        """追加一条 DM 消息到历史。

        Args:
            session_id: 当前 WS session id
            agent_key: 目标 agent key（6 九洲一号群角色之一）
            role: 'user' 或 'agent'
            text: 消息文本
            timestamp: ms 时间戳；None 时用当前时间
            agent_name: 仅 role='agent' 时填
            agent_emoji: 仅 role='agent' 时填

        Returns:
            持久化后的 DmMessage
        """
        if role not in ("user", "agent"):
            raise ValueError(f"role must be 'user' or 'agent', got {role!r}")
        if not text:
            raise ValueError("text must be non-empty")
        if not session_id or not agent_key:
            raise ValueError("session_id and agent_key are required")

        ts = timestamp if timestamp is not None else int(time.time() * 1000)

        with self._lock:
            self._conn.execute(
                """
                INSERT INTO dm_history
                    (session_id, agent_key, role, text, timestamp, agent_name, agent_emoji)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (session_id, agent_key, role, text, ts, agent_name, agent_emoji),
            )
            self._conn.commit()

        return DmMessage(
            role=role,
            text=text,
            timestamp=ts,
            agent_key=agent_key,
            agent_name=agent_name,
            agent_emoji=agent_emoji,
        )

    def count_messages(self, session_id: str, agent_key: str) -> int:
        """统计历史条数（用于 memory_size 徽章 / 测试断言）。"""
        if not session_id or not agent_key:
            return 0
        with self._lock:
            cur = self._conn.execute(
                "SELECT COUNT(*) AS n FROM dm_history WHERE session_id = ? AND agent_key = ?",
                (session_id, agent_key),
            )
            row = cur.fetchone()
        return int(row["n"]) if row else 0

    def list_sessions_for_agent(self, agent_key: str) -> list[str]:
        """列出某个 agent_key 下有过 DM 的所有 session_id（去重）。

        用于调试 / admin 视图；不在前端 dm_init 响应中暴露。
        """
        if not agent_key:
            return []
        with self._lock:
            cur = self._conn.execute(
                "SELECT DISTINCT session_id FROM dm_history WHERE agent_key = ? ORDER BY session_id",
                (agent_key,),
            )
            return [r["session_id"] for r in cur.fetchall()]

    def clear_history(self, session_id: str, agent_key: str) -> int:
        """清空某个 (session_id, agent_key) 的历史。返回删除条数。

        用于"清空聊天记录"按钮或测试 setup。
        """
        if not session_id or not agent_key:
            return 0
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM dm_history WHERE session_id = ? AND agent_key = ?",
                (session_id, agent_key),
            )
            self._conn.commit()
            return cur.rowcount


# ============================================================================
# 全局默认实例（生产代码用）+ 测试注入
# ============================================================================
_default_store: DmStore | None = None
_default_store_lock = threading.Lock()


def _default_db_path() -> str:
    """生产默认 SQLite 路径：backend/data/dm_history.sqlite。

    可被环境变量 `DM_DB_PATH` 覆盖。
    """
    env = os.environ.get("DM_DB_PATH")
    if env:
        return env
    # backend/data/dm_history.sqlite（相对于 backend/app/memory/dm_store.py）
    backend_root = Path(__file__).resolve().parents[2]
    data_dir = backend_root / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    return str(data_dir / "dm_history.sqlite")


def get_dm_store() -> DmStore:
    """获取（懒加载）全局默认 DM store。

    生产代码（routers/ws.py 等）通过本函数拿单例。
    测试可通过 `set_dm_store(...)` 注入 :memory: 或临时 SQLite。
    """
    global _default_store
    with _default_store_lock:
        if _default_store is None:
            _default_store = DmStore(_default_db_path())
        return _default_store


def set_dm_store(store: DmStore | None) -> DmStore | None:
    """注入 / 替换 / 关闭全局默认 DM store。返回旧实例。

    测试典型用法：
        from app.memory import get_dm_store, set_dm_store
        from app.memory.dm_store import DmStore
        old = set_dm_store(DmStore(":memory:"))
        try:
            ... 测试逻辑（get_dm_store() 拿到 :memory: 实例）...
        finally:
            set_dm_store(old)  # 还原
    """
    global _default_store
    with _default_store_lock:
        old = _default_store
        _default_store = store
        return old


def close_dm_store() -> None:
    """关闭并清空全局默认 DM store（用于 graceful shutdown / 测试 teardown）。"""
    set_dm_store(None)