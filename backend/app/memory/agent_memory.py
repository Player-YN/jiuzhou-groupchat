"""Per-agent unified source-aware memory store.

九洲一号群 6 角色 (shu-hang / yao-shi / san-lang / bei-he / bai-qianbei / ling-die)
每个人拥有**一份统一时间线 memory**。任何场景下 (group 或 dm) 发生的、与该角色相关
的事件,都进同一份 memory 并带 `source` 标记。

设计要点（详见 `.harness/reports/agent_memory_design.md`）：
- SQLite 单表 `agent_memory`（append-only），按 (session_id, agent_key, timestamp) 索引
- `source` 字段区分 group / dm
- `speaker_key` 字段记录实际发言者（user 或 6 角色之一）
- Group 事件 fan-out 到全部 6 角色 memory（九洲一号群是公开场景）
- DM 事件只写到 dm 双方（user + 目标 agent）
- 隐私保证：load 只返回指定 (session_id, agent_key) 的消息，**绝不跨 agent_key 返回**

与 DmStore (Stage 6 DM Phase 2) 关系：
- DmStore 旧 `dm_history` 表保留为 read-only，不强制迁移
- AgentMemoryStore 是 dm + group 的统一层，新代码全部走这里
"""
from __future__ import annotations

import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Literal

from app.models import AgentMemoryEntry


# ============================================================================
# Schema
# ============================================================================
_SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_memory (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT    NOT NULL,
    agent_key   TEXT    NOT NULL,
    role        TEXT    NOT NULL CHECK(role IN ('user', 'agent')),
    source      TEXT    NOT NULL CHECK(source IN ('group', 'dm')),
    speaker_key TEXT    NOT NULL,
    text        TEXT    NOT NULL,
    timestamp   INTEGER NOT NULL,
    agent_name  TEXT,
    agent_emoji TEXT,
    author      TEXT
);
CREATE INDEX IF NOT EXISTS idx_agent_memory_session_agent_time
    ON agent_memory(session_id, agent_key, timestamp ASC);
CREATE INDEX IF NOT EXISTS idx_agent_memory_session_source
    ON agent_memory(session_id, source);
"""


# T9 / Piece B: ALTER-TABLE migration for already-deployed DBs.
# SQLite IF NOT EXISTS on columns is unavailable, so we probe
# PRAGMA table_info and run ALTER TABLE only when missing.
# Default fill value: '神秘人' (anonymous cultivator) — same as frontend's
# userIdentity.DEFAULT_DISPLAY_NAME so existing rows display consistently
# even though the user never typed a name on those legacy entries.
_AUTHOR_COLUMN_DDL = "ALTER TABLE agent_memory ADD COLUMN author TEXT"
_AUTHOR_BACKFILL_DDL = (
    "UPDATE agent_memory SET author = ? WHERE author IS NULL AND role = 'user'"
)
_AUTHOR_BACKFILL_VALUE = "神秘人"


def _migrate_add_author_column(conn: sqlite3.Connection) -> None:
    """Add `author` column to existing agent_memory tables (T9 / Piece B).

    Idempotent: probes PRAGMA table_info; no-op if column already exists.
    Also backfills any pre-existing user rows (where role='user') with the
    default "神秘人" so the frontend never sees a NULL author on legacy rows.
    """
    cur = conn.execute("PRAGMA table_info(agent_memory)")
    cols = {row[1] for row in cur.fetchall()}
    if "author" in cols:
        # Column already there. Still make sure user rows aren't NULL.
        conn.execute(_AUTHOR_BACKFILL_DDL, (_AUTHOR_BACKFILL_VALUE,))
        conn.commit()
        return
    conn.execute(_AUTHOR_COLUMN_DDL)
    conn.execute(_AUTHOR_BACKFILL_DDL, (_AUTHOR_BACKFILL_VALUE,))
    conn.commit()


# ============================================================================
# Constants
# ============================================================================
# 九洲一号群 6 角色 — fan-out 默认 audience
ROLE_AGENT_KEYS: tuple[str, ...] = (
    "shu-hang",
    "yao-shi",
    "san-lang",
    "bei-he",
    "bai-qianbei",
    "ling-die",
)

Role = Literal["user", "agent"]
Source = Literal["group", "dm"]


# ============================================================================
# AgentMemoryStore
# ============================================================================
class AgentMemoryStore:
    """SQLite-backed per-agent unified source-aware memory.

    单实例可服务多 session / 多 agent；线程安全（通过 `threading.Lock` 串行化）。
    """

    def __init__(self, db_path: str | Path):
        self._db_path = str(db_path)
        self._lock = threading.Lock()
        # 允许并发读：check_same_thread=False，sqlite3 默认串行化是 OK 的
        # （写路径用 lock 保护）
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self.init_schema()

    # ----- 生命周期 -----
    def init_schema(self) -> None:
        """创建 agent_memory 表 + 索引。幂等。

        T9 / Piece B: also runs `_migrate_add_author_column` so that existing
        DBs created before the author column existed get ALTER-TABLE'd in
        place, and any pre-existing user rows are backfilled with the default
        "神秘人". Idempotent — safe to call on every startup.
        """
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()
            _migrate_add_author_column(self._conn)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ----- CRUD -----
    def load_agent_memory(
        self,
        session_id: str,
        agent_key: str,
    ) -> list[AgentMemoryEntry]:
        """读取某 agent 完整时间线，按 timestamp 升序。

        **隐私保证**：只返回指定 agent_key 的消息；绝不跨 agent_key 泄漏。
        即使调用方传错 agent_key，也只会返回空列表（SQL 严格按 WHERE 过滤）。

        返回的列表混合 group + dm 两种 source，按时间排序。
        """
        if not session_id or not agent_key:
            return []
        with self._lock:
            cur = self._conn.execute(
                """
                SELECT role, source, speaker_key, text, timestamp,
                       agent_key, agent_name, agent_emoji, author
                FROM agent_memory
                WHERE session_id = ? AND agent_key = ?
                ORDER BY timestamp ASC, id ASC
                """,
                (session_id, agent_key),
            )
            rows = cur.fetchall()
        return [
            AgentMemoryEntry(
                role=r["role"],
                source=r["source"],
                speaker_key=r["speaker_key"],
                text=r["text"],
                timestamp=r["timestamp"],
                agent_key=r["agent_key"],
                agent_name=r["agent_name"],
                agent_emoji=r["agent_emoji"],
                author=r["author"],
            )
            for r in rows
        ]

    def append_message(
        self,
        session_id: str,
        agent_key: str,                # memory owner
        role: Role,
        source: Source,
        speaker_key: str,              # 实际发言者
        text: str,
        timestamp: int | None = None,
        agent_name: str | None = None,
        agent_emoji: str | None = None,
        author: str | None = None,    # T9: user-typed 时填,默认 '神秘人'
    ) -> AgentMemoryEntry:
        """追加一条消息到指定 agent 的 memory。

        Args:
            session_id: 当前 WS session id
            agent_key: memory owner（6 九洲一号群角色之一）
            role: 'user' 或 'agent'
            source: 'group' 或 'dm'
            speaker_key: 实际发言者 ('user' 或 6 角色 key)
            text: 消息文本
            timestamp: ms 时间戳；None 时用当前时间
            agent_name: 当 speaker 是 agent 时填
            agent_emoji: 当 speaker 是 agent 时填
            author: T9 / Piece B: 用户署名（user-typed 时填）— 当 role='user'
                    且 author 为 None 时 fallback 到 '神秘人'。AI-typed 时通常
                    为 None（agent 不署 user 名）。

        Returns:
            持久化后的 AgentMemoryEntry
        """
        if role not in ("user", "agent"):
            raise ValueError(f"role must be 'user' or 'agent', got {role!r}")
        if source not in ("group", "dm"):
            raise ValueError(f"source must be 'group' or 'dm', got {source!r}")
        if not text or not text.strip():
            raise ValueError("text must be non-empty")
        if not session_id or not agent_key:
            raise ValueError("session_id and agent_key are required")
        if not speaker_key:
            raise ValueError("speaker_key is required")

        # T9 / Piece B: 当 user-typed 且 author 未传 → fallback "T9_DEFAULT" (与
        # 前端 userIdentity.DEFAULT_DISPLAY_NAME 保持一致)。AI-typed 时
        # author 通常为 None (不需要给 agent 署 user 名)。
        effective_author: str | None
        if role == "user":
            trimmed_author = (author or "").strip() if isinstance(author, str) else ""
            effective_author = trimmed_author if trimmed_author else _AUTHOR_BACKFILL_VALUE
        else:
            effective_author = None

        ts = timestamp if timestamp is not None else int(time.time() * 1000)

        with self._lock:
            self._conn.execute(
                """
                INSERT INTO agent_memory
                    (session_id, agent_key, role, source, speaker_key,
                     text, timestamp, agent_name, agent_emoji, author)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id, agent_key, role, source, speaker_key,
                    text, ts, agent_name, agent_emoji, effective_author,
                ),
            )
            self._conn.commit()

        return AgentMemoryEntry(
            role=role,
            source=source,
            speaker_key=speaker_key,
            text=text,
            timestamp=ts,
            agent_key=agent_key,
            agent_name=agent_name,
            agent_emoji=agent_emoji,
            author=effective_author,
        )

    def fan_out_group_event(
        self,
        session_id: str,
        speaker_key: str,
        text: str,
        timestamp: int | None = None,
        agent_name: str | None = None,
        agent_emoji: str | None = None,
        role: Role = "agent",
        audience: list[str] | None = None,
        author: str | None = None,    # T9: user-typed group event 时的署名
    ) -> list[AgentMemoryEntry]:
        """Group 事件 fan-out：一条事件写入 audience 中每个角色的 memory。

        九洲一号群是公开场景，每个人都"在场"，所以默认 fan-out 到全部 6 角色。
        调用方也可以传 audience 指定部分角色（用于未来支持旁听场景）。

        Args:
            session_id: 当前 WS session id
            speaker_key: 实际发言者 ('user' 或 6 角色 key)
            text: 消息文本
            timestamp: ms 时间戳；None 时用当前时间
            agent_name: 当 speaker 是 agent 时填
            agent_emoji: 当 speaker 是 agent 时填
            role: 'user' 或 'agent'
            audience: 接收这条事件的角色列表；None 时默认全部 6 角色
            author: T9 / Piece B: 用户署名 — group 事件 user-typed 时填,
                    AI-typed 时 None。fallback 逻辑与 append_message 一致。

        Returns:
            写入的 AgentMemoryEntry 列表（用于日志 / 测试断言）
        """
        if not session_id or not speaker_key:
            raise ValueError("session_id and speaker_key are required")
        if not text or not text.strip():
            raise ValueError("text must be non-empty")

        # T9 / Piece B: 与 append_message 一致的 author fallback 逻辑
        if role == "user":
            trimmed_author = (author or "").strip() if isinstance(author, str) else ""
            effective_author: str | None = trimmed_author if trimmed_author else _AUTHOR_BACKFILL_VALUE
        else:
            effective_author = None

        if audience is None:
            audience = list(ROLE_AGENT_KEYS)

        ts = timestamp if timestamp is not None else int(time.time() * 1000)

        entries: list[AgentMemoryEntry] = []
        with self._lock:
            for agent_key in audience:
                self._conn.execute(
                    """
                    INSERT INTO agent_memory
                        (session_id, agent_key, role, source, speaker_key,
                         text, timestamp, agent_name, agent_emoji, author)
                    VALUES (?, ?, ?, 'group', ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        session_id, agent_key, role, speaker_key,
                        text, ts, agent_name, agent_emoji, effective_author,
                    ),
                )
                entries.append(
                    AgentMemoryEntry(
                        role=role,
                        source="group",
                        speaker_key=speaker_key,
                        text=text,
                        timestamp=ts,
                        agent_key=agent_key,
                        agent_name=agent_name,
                        agent_emoji=agent_emoji,
                        author=effective_author,
                    )
                )
            self._conn.commit()

        return entries

    def count_messages(self, session_id: str, agent_key: str) -> int:
        """统计 memory 条数（用于 memory_size 徽章 / 测试断言）。"""
        if not session_id or not agent_key:
            return 0
        with self._lock:
            cur = self._conn.execute(
                "SELECT COUNT(*) AS n FROM agent_memory WHERE session_id = ? AND agent_key = ?",
                (session_id, agent_key),
            )
            row = cur.fetchone()
        return int(row["n"]) if row else 0

    def list_sessions_for_agent(self, agent_key: str) -> list[str]:
        """列出某个 agent_key 下有过 memory 的所有 session_id（去重）。

        用于调试 / admin 视图；不在前端 dm_init 响应中暴露。
        """
        if not agent_key:
            return []
        with self._lock:
            cur = self._conn.execute(
                "SELECT DISTINCT session_id FROM agent_memory WHERE agent_key = ? ORDER BY session_id",
                (agent_key,),
            )
            return [r["session_id"] for r in cur.fetchall()]

    def load_recent_group_events(self, *, limit: int = 20) -> list[AgentMemoryEntry]:
        """加载最近的群聊事件（跨所有 session，source='group'，按时间倒序）。

        Stage 8-NPC-Love（ADR-0007）：每个 NPC 的自主 loop 调用本方法拿到"群里最近
        20 条事件"作为决策上下文（与具体 session_id 解耦——9 洲一号群 NPC 看到的
        是"整个群"的近况，而不是某一个 WS session 的局部视图）。

        返回的列表按 timestamp DESC 排序（最新在前）。调用方通常会再 reverse 一下
        以便按时间正序喂给 LLM。

        Args:
            limit: 最多返回多少条；<=0 时返回空列表

        Returns:
            AgentMemoryEntry 列表（最新在前）
        """
        if limit <= 0:
            return []
        with self._lock:
            cur = self._conn.execute(
                """
                SELECT role, source, speaker_key, text, timestamp,
                       agent_key, agent_name, agent_emoji, author
                FROM agent_memory
                WHERE source = 'group'
                ORDER BY timestamp DESC, id DESC
                LIMIT ?
                """,
                (int(limit),),
            )
            rows = cur.fetchall()
        return [
            AgentMemoryEntry(
                role=r["role"],
                source=r["source"],
                speaker_key=r["speaker_key"],
                text=r["text"],
                timestamp=r["timestamp"],
                agent_key=r["agent_key"],
                agent_name=r["agent_name"],
                agent_emoji=r["agent_emoji"],
                author=r["author"],
            )
            for r in rows
        ]

    def load_session_group_history(
        self,
        session_id: str,
        *,
        limit: int = 20,
    ) -> list[AgentMemoryEntry]:
        """Load one logical copy of recent group events for a UI session.

        Group fan-out writes one row per audience member. Querying the stable
        ``shu-hang`` audience timeline yields exactly one copy per public event,
        ordered oldest-to-newest for prompts and deterministic policy context.
        """
        if not session_id or limit <= 0:
            return []
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT role, source, speaker_key, text, timestamp,
                       agent_key, agent_name, agent_emoji, author
                FROM (
                    SELECT * FROM agent_memory
                    WHERE session_id = ? AND source = 'group' AND agent_key = 'shu-hang'
                    ORDER BY timestamp DESC, id DESC
                    LIMIT ?
                )
                ORDER BY timestamp ASC, id ASC
                """,
                (session_id, int(limit)),
            ).fetchall()
        return [
            AgentMemoryEntry(
                role=row["role"],
                source=row["source"],
                speaker_key=row["speaker_key"],
                text=row["text"],
                timestamp=row["timestamp"],
                agent_key=row["agent_key"],
                agent_name=row["agent_name"],
                agent_emoji=row["agent_emoji"],
                author=row["author"],
            )
            for row in rows
        ]

    def clear_agent_memory(self, session_id: str, agent_key: str) -> int:
        """清空某个 (session_id, agent_key) 的 memory。返回删除条数。

        用于"清空聊天记录"按钮或测试 setup。
        """
        if not session_id or not agent_key:
            return 0
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM agent_memory WHERE session_id = ? AND agent_key = ?",
                (session_id, agent_key),
            )
            self._conn.commit()
            return cur.rowcount

    def delete_session(
        self,
        *,
        session_id: str,
        agent_key: str | None = None,
        source: str | None = None,
    ) -> int:
        """T9 / Piece C: 按 (session_id, [agent_key], [source]) 删除行, 返回条数。

        - session_id 必传（路径固定）。
        - agent_key 可选: None = 删这个 session_id 下**所有角色**的所有行;
          str = 只删该 (session_id, agent_key) 行。
        - source 可选: None = 不限定 (group + dm 都删);
          'group' / 'dm' = 只删对应 source 的行。

        用法:
          # 清空群聊窗口 (只 source='group', 全 agent)
          store.delete_session(session_id='xxx', source='group')

          # 清空 DM 窗口 (只 source='dm', 单 agent target)
          store.delete_session(session_id='xxx', agent_key='shu-hang', source='dm')

          # 清空 session 全部 (group + dm × 所有 agent)
          store.delete_session(session_id='xxx')

        Cross-session isolation: session_id 唯一区分, 不会误删其他 session 的行。
        """
        if not session_id:
            return 0
        # Build WHERE 条件 + params
        clauses = ["session_id = ?"]
        params: list[Any] = [session_id]
        if agent_key is not None:
            if not agent_key:
                return 0
            clauses.append("agent_key = ?")
            params.append(agent_key)
        if source is not None:
            if source not in ("group", "dm"):
                raise ValueError(f"source must be 'group' or 'dm' or None, got {source!r}")
            clauses.append("source = ?")
            params.append(source)

        where_sql = " AND ".join(clauses)
        with self._lock:
            cur = self._conn.execute(
                f"DELETE FROM agent_memory WHERE {where_sql}",
                tuple(params),
            )
            self._conn.commit()
            return cur.rowcount


# ============================================================================
# 全局默认实例（生产代码用）+ 测试注入
# ============================================================================
_default_store: AgentMemoryStore | None = None
_default_store_lock = threading.Lock()


def _default_db_path() -> str:
    """生产默认 SQLite 路径：backend/data/agent_memory.sqlite。

    可被环境变量 `AGENT_MEMORY_DB_PATH` 覆盖。
    """
    env = os.environ.get("AGENT_MEMORY_DB_PATH")
    if env:
        return env
    # backend/data/agent_memory.sqlite（相对于 backend/app/memory/agent_memory.py）
    backend_root = Path(__file__).resolve().parents[2]
    data_dir = backend_root / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    return str(data_dir / "agent_memory.sqlite")


def get_agent_memory_store() -> AgentMemoryStore:
    """获取（懒加载）全局默认 agent memory store。

    生产代码（graph.py / routers/ws.py 等）通过本函数拿单例。
    测试可通过 `set_agent_memory_store(...)` 注入 :memory: 或临时 SQLite。
    """
    global _default_store
    with _default_store_lock:
        if _default_store is None:
            _default_store = AgentMemoryStore(_default_db_path())
        return _default_store


def set_agent_memory_store(store: AgentMemoryStore | None) -> AgentMemoryStore | None:
    """注入 / 替换 / 关闭全局默认 agent memory store。返回旧实例。

    测试典型用法：
        from app.memory import get_agent_memory_store, set_agent_memory_store
        from app.memory.agent_memory import AgentMemoryStore
        old = set_agent_memory_store(AgentMemoryStore(":memory:"))
        try:
            ... 测试逻辑 ...
        finally:
            set_agent_memory_store(old)  # 还原
    """
    global _default_store
    with _default_store_lock:
        old = _default_store
        _default_store = store
        return old


def close_agent_memory_store() -> None:
    """关闭并清空全局默认 agent memory store（用于 graceful shutdown / 测试 teardown）。"""
    set_agent_memory_store(None)
