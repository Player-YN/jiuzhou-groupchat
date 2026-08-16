"""Pydantic schemas — WebSocket 协议定义。

前后端共用的消息协议。消息统一格式：
    { "type": "...", "session_id": "...", "payload": {...}, "ts": <ms> }

类型（群聊 + 私信两套，统一在同一个 WS 连接上）：

**群聊（group chat）**：
    - session_init：WS 握手成功后由服务端推送
    - user_msg_ack：服务端确认收到用户消息
    - supervisor_decision：Supervisor 选定下一位发言者
    - agent_thinking / agent_msg_chunk / agent_done：单 Agent 流式输出
    - max_rounds_reached / group_chat_done：群聊结束
    - error / ping / pong：异常 + 心跳

**私信（DM，Stage 6 DM Phase 2）**：
    - 客户端 → 服务端：dm_init（握手 + 切到 DM 模式）/ dm_msg（发私信）/ dm_interrupt
    - 服务端 → 客户端：dm_init（带回历史）/ dm_msg_ack / dm_thinking / dm_msg_chunk /
                       dm_done / dm_error

DM 与群聊在同一个 WS 连接上共存；DM 与群聊互斥（同一时间只允许一种模式），
切换模式（DM ↔ 群聊）需要重连 WS，避免 state machine 过于复杂。
"""
from __future__ import annotations

import time
from typing import Any, Literal

from pydantic import BaseModel, Field


# ===== 客户端 → 服务端（统一） =====
class ClientMessage(BaseModel):
    """客户端发往服务端的所有消息的统一 schema。

    - 群聊：type ∈ {"user_msg", "ping", "interrupt"}
    - DM  ：type ∈ {"dm_init", "dm_msg", "dm_interrupt", "ping"}
    """

    type: Literal[
        # --- 群聊 ---
        "user_msg",
        "interrupt",
        # --- 私信 ---
        "dm_init",
        "dm_msg",
        "dm_interrupt",
        # --- 通用 ---
        "ping",
    ]
    session_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class UserMsgPayload(BaseModel):
    """群聊 user_msg 的 payload。"""

    text: str
    msg_id: str | None = None
    author: str | None = None


class DmInitPayload(BaseModel):
    """dm_init 的 payload。

    客户端首次连接时（或切换目标 AI 时）发送 dm_init，
    服务端回 dm_init 并附带历史消息。
    """

    target_agent: str = Field(
        ...,
        description="目标 agent key，6 九洲一号群角色之一: shu-hang/yao-shi/san-lang/bei-he/bai-qianbei/ling-die",
    )


class DmMsgPayload(BaseModel):
    """dm_msg 的 payload（用户发给目标 AI 的私信文本）。"""

    text: str
    msg_id: str | None = None
    author: str | None = None


# ===== 服务端 → 客户端（统一） =====
# 所有 DM 与群聊的 type 字段全部展开在一个 Literal 里，便于前端做穷举 switch。
ServerMessageType = Literal[
    # --- 群聊 ---
    "session_init",
    "user_msg_ack",
    "supervisor_decision",
    "agent_thinking",
    "agent_msg_chunk",
    "agent_done",
    "max_rounds_reached",
    "group_chat_done",
    "cron_agent_post",
    # --- 私信（Stage 6 DM Phase 2）---
    "dm_init",  # 响应客户端 dm_init（带回历史 + target_agent 元数据）
    "dm_msg_ack",  # 确认收到 dm_msg / dm_interrupt
    "dm_thinking",  # 目标 AI 开始思考
    "dm_msg_chunk",  # 流式 token
    "dm_done",  # 目标 AI 完成回复
    "dm_error",  # DM 路径异常
    # --- 通用 ---
    "error",
    "pong",
]


class ServerMessage(BaseModel):
    """服务端发往客户端的所有消息的统一 schema。"""

    type: ServerMessageType
    session_id: str
    payload: dict[str, Any] = Field(default_factory=dict)
    ts: int = Field(default_factory=lambda: int(time.time() * 1000))


# ===== DM 路径的 payload 形状（参考用，方便前端 TypeScript 对齐） =====
class DmMessage(BaseModel):
    """DM 历史消息条目（持久化层 + 服务端推送统一格式）。

    role:
        - "user":  用户发的
        - "agent": 目标 AI 回的
    """

    role: Literal["user", "agent"]
    text: str
    timestamp: int
    # 当 role == "agent" 时填，目标 AI 的元数据（便于前端渲染头像）
    agent_key: str | None = None
    agent_name: str | None = None
    agent_emoji: str | None = None


class AgentMemoryEntry(BaseModel):
    """统一 memory entry — dm_init 响应 + AgentMemoryStore.load 返回类型用。

    Stage 7 Bug 2 per-agent unified memory 架构的核心 schema:
    - `role`: 消息角色（user / agent）
    - `source`: 消息来源（group / dm），前端可按 source 渲染主题
    - `speaker_key`: 实际发言者（"user" 或 6 修真角色 key 之一）
    - `agent_key`: memory owner (= load_agent_memory 传入的 agent_key)
    - `text` / `timestamp` / `agent_name` / `agent_emoji`: 渲染元数据

    T9 / Piece B 新增字段:
    - `author`: 消息的人类署名（user-typed 时填，AI-typed 时为 None）。
      前端 userIdentity 默认 "神秘人"，可改；持久化到 SQLite 后，跨刷新
      渲染时仍能正确署名（即便前端 localStorage 被清空也不会丢）。
      向后兼容：旧 rows 默认填 "神秘人"，schema 层加列时 ALTER TABLE 填默认。
    """

    role: Literal["user", "agent"]
    source: Literal["group", "dm"]
    speaker_key: str  # "user" 或 6 角色 key
    text: str
    timestamp: int
    agent_key: str | None = None        # memory owner (= load_agent_memory 传入的)
    agent_name: str | None = None       # speaker 是 agent 时填
    agent_emoji: str | None = None
    author: str | None = None           # T9: 用户署名（user-typed entry 时）


class DmInitResponsePayload(BaseModel):
    """dm_init 响应 payload（服务端 → 客户端）。

    Stage 7 Bug 2: history 类型从 DmMessage[] 升级 AgentMemoryEntry[]。
    AgentMemoryEntry 含 `source` (group/dm) + `speaker_key` 字段,前端可按 source 渲染背景。
    老前端忽略未知字段即可继续工作(向后兼容)。

    target_agent: 目标 agent key
    name: 中文名（前端渲染头像 / 标题）
    emoji: emoji 头像
    history: 统一 memory 条目列表（group + dm 混合，按时间排序）
    memory_size: history 长度
    """

    target_agent: str
    name: str
    emoji: str
    history: list[AgentMemoryEntry] = Field(default_factory=list)
    memory_size: int = 0


def make_msg(msg_type: str, session_id: str, **payload: Any) -> dict[str, Any]:
    """构造一条服务端消息 dict（直接 JSON-serializable）。"""
    return {
        "type": msg_type,
        "session_id": session_id,
        "payload": payload,
        "ts": int(time.time() * 1000),
    }
