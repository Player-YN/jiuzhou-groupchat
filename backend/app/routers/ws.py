"""WebSocket router — `/ws/{session_id}` 端点。

协议实现见 `02_架构设计.md` § 4 + Stage 6 DM Phase 2 DM 协议扩展。
本文件只做"消息分发 + stream 转发"，具体 Agent 推理逻辑在 `app.graph.stream_*` 里。

支持的 client → server 消息类型：
    - ping：心跳
    - user_msg：群聊用户消息（→ stream_group_chat，事件翻译成 agent_*）
    - interrupt：群聊打断（当前实现仅 ack）
    - dm_init：私信握手 + 切到 DM 模式（→ 加载历史，回 dm_init）
    - dm_msg：DM 用户消息（→ stream_dm_chat，事件翻译成 dm_*）
    - dm_interrupt：DM 打断（仅 ack）

DM 与群聊共享同一个 WS 连接，互斥：发 `dm_init` 后进入 DM 模式直到断连。

Stage 7 Bug 2 增强:
- 持久化层切换: get_dm_store() -> get_agent_memory_store() (per-agent 统一 memory)
- dm_init history 返回 AgentMemoryEntry[] (含 source 字段,前端可按 group/dm 渲染)
- dm_msg: 用 AgentMemoryStore.append_message(source="dm", ...)
- user_msg: 传入 session_id + memory_store 给 stream_group_chat (内部 fan-out)
"""
from __future__ import annotations

import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.behavior import BehaviorEngine, BehaviorEvent, get_decision_log_store
from app.graph import ROLES, stream_dm_chat, stream_group_chat
from app.memory import get_agent_memory_store
from app.models import make_msg
from app.scheduler.connection_registry import get_connection_registry

logger = logging.getLogger(__name__)

router = APIRouter()


@router.websocket("/ws/{session_id}")
async def ws_endpoint(websocket: WebSocket, session_id: str) -> None:
    """WebSocket endpoint: 群聊 + 私信统一入口。

    群聊路径：
        接受 → 推 `session_init`（6 九洲一号群角色元数据）→ 读循环分类处理

    DM 路径（Stage 6 DM Phase 2）：
        客户端发 `dm_init{target_agent}` → 服务端加载 (session_id, target_agent) 历史，
        回 `dm_init{target_agent, name, emoji, history, memory_size}`。
        后续 `dm_msg{text}` → stream_dm_chat → 翻译成 `dm_msg_ack / dm_thinking /
        dm_msg_chunk / dm_done / dm_error`，并把 user + agent 双方消息持久化到 DmStore。
    """
    await websocket.accept()
    logger.info("WS connected: session_id=%s", session_id)

    # Register the live connection so the cron proactive services can push
    # events to it.  We unregister in the finally block below.
    get_connection_registry().register(session_id, websocket)

    # 1. 握手：推 session_init（群聊元数据；DM 不重发这个，DM 走 dm_init 单独拿历史）
    # Stage 4-B：6 九洲一号群角色 (中文名 + emoji 顺序固定: shu-hang → yao-shi → san-lang → bei-he → bai-qianbei → ling-die)
    await websocket.send_json(
        make_msg(
            "session_init",
            session_id,
            agents=[
                "宋书航 🌟", "药师 💊", "狂刀三浪 🗡️",
                "北河散人 🌊", "白前辈 👻", "灵蝶尊者 🦋",
            ],
            topic=None,
        )
    )

    # Stage 6 DM Phase 2: 当前 DM 目标 agent (None = 群聊模式 / DM 未激活)
    dm_target_agent: str | None = None

    try:
        while True:
            try:
                raw = await websocket.receive_text()
            except WebSocketDisconnect:
                logger.info("WS disconnected: session_id=%s", session_id)
                break

            # 解析失败也要让连接活着
            try:
                data = json.loads(raw)
            except json.JSONDecodeError as e:
                await websocket.send_json(
                    make_msg("error", session_id, code="BAD_JSON", message=str(e))
                )
                continue

            msg_type = data.get("type")
            payload = data.get("payload") or {}

            # ===== 通用心跳 =====
            if msg_type == "ping":
                await websocket.send_json(make_msg("pong", session_id))
                continue

            # ===== 群聊 interrupt =====
            if msg_type == "interrupt":
                logger.info("group interrupt received: session_id=%s", session_id)
                await websocket.send_json(
                    make_msg("user_msg_ack", session_id, status="interrupted")
                )
                continue

            # ===== 群聊 user_msg =====
            if msg_type == "user_msg":
                # DM 模式下忽略群聊消息（前端不该这样调；保底不让它污染 DM 流）
                if dm_target_agent is not None:
                    await websocket.send_json(
                        make_msg(
                            "error",
                            session_id,
                            code="MODE_CONFLICT",
                            message=(
                                f"当前处于 DM 模式 (target={dm_target_agent})，"
                                "无法发群聊消息；请重新连接 WS 后再发 user_msg。"
                            ),
                        )
                    )
                    continue

                text = payload.get("text") or data.get("text") or ""
                if not text.strip():
                    await websocket.send_json(
                        make_msg("error", session_id, code="EMPTY_TEXT", message="text is empty")
                    )
                    continue

                # T9 / Piece B: 读取 payload.author (前端 userIdentity 设的署名),
                # trim 后透传给 stream_group_chat → fan_out_group_event。
                # 缺省 None 时, fan_out_group_event 内部会 fallback 到 "神秘人"。
                group_author = (payload.get("author") or "").strip() or None
                # MVP Candidate: frontend msg_id is the stable event id used by
                # the behavior-decision log to suppress reconnect/retry duplicates.
                group_event_id = (payload.get("msg_id") or data.get("event_id") or "").strip() or None

                # ack 用户消息
                await websocket.send_json(
                    make_msg("user_msg_ack", session_id, text=text, event_id=group_event_id)
                )

                # MVP Candidate: behavior engine selects 0..2 responders.
                try:
                    async for ev in stream_group_chat(
                        user_text=text,
                        max_rounds=2,
                        session_id=session_id,
                        memory_store=get_agent_memory_store(),
                        author=group_author,
                        event_id=group_event_id,
                    ):
                        etype = ev.get("event")
                        if etype == "supervisor_decision":
                            await websocket.send_json(
                                make_msg(
                                    "supervisor_decision",
                                    session_id,
                                    next_agent=ev.get("next_agent"),
                                )
                            )
                        elif etype == "agent_thinking":
                            await websocket.send_json(
                                make_msg(
                                    "agent_thinking",
                                    session_id,
                                    agent=ev.get("agent"),
                                    name=ev.get("name"),
                                    emoji=ev.get("emoji"),
                                )
                            )
                        elif etype == "agent_msg_chunk":
                            await websocket.send_json(
                                make_msg(
                                    "agent_msg_chunk",
                                    session_id,
                                    agent=ev.get("agent"),
                                    chunk=ev.get("chunk", ""),
                                )
                            )
                        elif etype == "agent_done":
                            await websocket.send_json(
                                make_msg(
                                    "agent_done",
                                    session_id,
                                    agent=ev.get("agent"),
                                    name=ev.get("name"),
                                    emoji=ev.get("emoji"),
                                    full_text=ev.get("full_text", ""),
                                    round=ev.get("round"),
                                )
                            )
                        elif etype == "max_rounds_reached":
                            await websocket.send_json(
                                make_msg(
                                    "max_rounds_reached",
                                    session_id,
                                    max_rounds=ev.get("max_rounds"),
                                )
                            )
                        elif etype == "group_chat_done":
                            await websocket.send_json(
                                make_msg(
                                    "group_chat_done",
                                    session_id,
                                    rounds=ev.get("rounds", 0),
                                    agents=ev.get("agents", []),
                                )
                            )
                        elif etype == "error":
                            await websocket.send_json(
                                make_msg(
                                    "error",
                                    session_id,
                                    code=ev.get("code", "AGENT_ERROR"),
                                    message=ev.get("message", "unknown"),
                                )
                            )
                except Exception as e:  # noqa: BLE001
                    logger.exception("stream_group_chat failed: session_id=%s", session_id)
                    await websocket.send_json(
                        make_msg(
                            "error",
                            session_id,
                            code="STREAM_EXCEPTION",
                            message=str(e),
                        )
                    )
                continue

            # ===== Stage 6 DM Phase 2: 私信 dm_init =====
            if msg_type == "dm_init":
                target = (payload.get("target_agent") or "").strip()
                if not target:
                    await websocket.send_json(
                        make_msg(
                            "dm_error",
                            session_id,
                            code="EMPTY_TARGET",
                            message="dm_init 需要 payload.target_agent",
                        )
                    )
                    continue
                if target not in ROLES:
                    await websocket.send_json(
                        make_msg(
                            "dm_error",
                            session_id,
                            code="UNKNOWN_AGENT",
                            message=(
                                f"未知 target_agent: {target!r}; "
                                f"合法值: {sorted(ROLES.keys())}"
                            ),
                        )
                    )
                    continue

                # 加载该 (session_id, target_agent) 的统一 memory (group + dm)
                # Stage 7 Bug 2: 用 AgentMemoryStore (per-agent 统一 source-aware memory)
                store = get_agent_memory_store()
                history = store.load_agent_memory(
                    session_id=session_id, agent_key=target
                )
                memory_size = len(history)
                role = ROLES[target]

                # 切到 DM 模式
                dm_target_agent = target
                logger.info(
                    "DM init: session_id=%s target=%s history=%d msgs (group+dm unified)",
                    session_id,
                    target,
                    memory_size,
                )

                # 回 dm_init（带回 target 元数据 + 历史）
                # Stage 7 Bug 2: history 类型升级 AgentMemoryEntry[] (含 source/speaker_key)
                await websocket.send_json(
                    make_msg(
                        "dm_init",
                        session_id,
                        target_agent=target,
                        name=role["name"],
                        emoji=role["emoji"],
                        history=[m.model_dump() for m in history],
                        memory_size=memory_size,
                    )
                )
                continue

            # ===== Stage 6 DM Phase 2: 私信 dm_msg =====
            if msg_type == "dm_msg":
                # 必须先 dm_init
                if dm_target_agent is None:
                    await websocket.send_json(
                        make_msg(
                            "dm_error",
                            session_id,
                            code="NOT_IN_DM_MODE",
                            message="请先发 dm_init 选择目标 agent",
                        )
                    )
                    continue

                text = (payload.get("text") or "").strip()
                if not text:
                    await websocket.send_json(
                        make_msg(
                            "dm_error",
                            session_id,
                            code="EMPTY_TEXT",
                            message="dm_msg.text 不能为空",
                        )
                    )
                    continue

                target = dm_target_agent
                role = ROLES[target]
                dm_event_id = (payload.get("msg_id") or "").strip() or None

                # DM skips semantic arbitration, but shares the append-once
                # audit log. Explicit target mention deterministically selects
                # exactly the DM recipient.
                if dm_event_id is not None:
                    dm_event = BehaviorEvent(
                        event_id=dm_event_id,
                        session_id=f"dm:{session_id}:{target}",
                        event_type="user_message",
                        text=f"@{target} {text}",
                        speaker_key="user",
                    )
                    log_store = get_decision_log_store()
                    existing = log_store.get(dm_event_id)
                    if existing is not None:
                        if log_store.matches_event(dm_event) is False:
                            await websocket.send_json(make_msg(
                                "dm_error",
                                session_id,
                                code="EVENT_ID_COLLISION",
                                message="msg_id was already used for different DM input",
                            ))
                        else:
                            await websocket.send_json(make_msg(
                                "dm_msg_ack",
                                session_id,
                                target_agent=target,
                                text=text,
                                event_id=dm_event_id,
                                status="duplicate",
                            ))
                        continue
                    dm_decision = BehaviorEngine().decide(dm_event, [], max_responders=1)
                    if dm_decision.selected_roles != [target]:
                        await websocket.send_json(make_msg(
                            "dm_error",
                            session_id,
                            code="DM_TARGET_POLICY_FAILED",
                            message="deterministic DM target selection failed",
                        ))
                        continue
                    if not log_store.save(dm_decision):
                        await websocket.send_json(make_msg(
                            "dm_msg_ack",
                            session_id,
                            target_agent=target,
                            text=text,
                            event_id=dm_event_id,
                            status="duplicate",
                        ))
                        continue

                # ack DM 消息
                await websocket.send_json(
                    make_msg(
                        "dm_msg_ack",
                        session_id,
                        target_agent=target,
                        text=text,
                        event_id=dm_event_id,
                    )
                )

                # 1) 持久化 user 消息到 AgentMemoryStore (Stage 7 Bug 2: per-agent 统一 memory)
                # T9 / Piece B: payload.author 是前端 userIdentity 设的署名,
                # 缺省 fallback 到 "神秘人" (append_message 内部处理).
                dm_author = (payload.get("author") or "").strip()
                store = get_agent_memory_store()
                try:
                    store.append_message(
                        session_id=session_id,
                        agent_key=target,
                        role="user",
                        source="dm",
                        speaker_key="user",
                        text=text,
                        author=dm_author or None,
                    )
                except Exception as e:  # noqa: BLE001
                    logger.exception("dm append user failed: session=%s", session_id)
                    await websocket.send_json(
                        make_msg(
                            "dm_error",
                            session_id,
                            code="MEMORY_APPEND_FAILED",
                            message=str(e),
                        )
                    )
                    continue

                # 2) 流式调用 stream_dm_chat
                #    Stage 7 Bug 2: history 传 None,让 stream_dm_chat 自己从
                #    AgentMemoryStore.load_agent_memory 读统一 memory(包含 group + dm)
                #    不再传 history[:-1] 避免手动去重刚 append 的 user
                full_response = ""
                try:
                    async for ev in stream_dm_chat(
                        target_agent_key=target,
                        user_text=text,
                        history=None,
                        session_id=session_id,
                        memory_store=store,
                    ):
                        etype = ev.get("event")
                        if etype == "dm_thinking":
                            await websocket.send_json(
                                make_msg(
                                    "dm_thinking",
                                    session_id,
                                    agent=ev.get("agent"),
                                    name=ev.get("name"),
                                    emoji=ev.get("emoji"),
                                )
                            )
                        elif etype == "dm_msg_chunk":
                            await websocket.send_json(
                                make_msg(
                                    "dm_msg_chunk",
                                    session_id,
                                    agent=ev.get("agent"),
                                    chunk=ev.get("chunk", ""),
                                )
                            )
                        elif etype == "dm_done":
                            full_response = ev.get("full_text", "")
                            await websocket.send_json(
                                make_msg(
                                    "dm_done",
                                    session_id,
                                    agent=ev.get("agent"),
                                    name=ev.get("name"),
                                    emoji=ev.get("emoji"),
                                    full_text=full_response,
                                )
                            )
                        elif etype == "dm_error":
                            await websocket.send_json(
                                make_msg(
                                    "dm_error",
                                    session_id,
                                    code=ev.get("code", "DM_STREAM_ERROR"),
                                    message=ev.get("message", "unknown"),
                                )
                            )
                except Exception as e:  # noqa: BLE001
                    logger.exception("stream_dm_chat failed: session=%s", session_id)
                    await websocket.send_json(
                        make_msg(
                            "dm_error",
                            session_id,
                            code="STREAM_EXCEPTION",
                            message=str(e),
                        )
                    )
                    continue

                # 4) 持久化 agent 消息 — Stage 7 Bug 2: stream_dm_chat 内部已自动持久化
                #    (AgentMemoryStore.append_message source="dm" speaker_key=target)
                #    不再需要 ws.py 手动 append(避免重复)
                continue

            # ===== Stage 6 DM Phase 2: 私信 dm_interrupt =====
            if msg_type == "dm_interrupt":
                logger.info(
                    "dm_interrupt: session_id=%s target=%s",
                    session_id,
                    dm_target_agent,
                )
                await websocket.send_json(
                    make_msg(
                        "dm_msg_ack",
                        session_id,
                        target_agent=dm_target_agent,
                        status="interrupted",
                    )
                )
                continue

            # ===== 未知类型 =====
            await websocket.send_json(
                make_msg(
                    "error",
                    session_id,
                    code="UNKNOWN_TYPE",
                    message=f"unsupported message type: {msg_type}",
                )
            )
    except WebSocketDisconnect:
        logger.info("WS disconnected (outer): session_id=%s", session_id)
    except Exception as e:  # noqa: BLE001
        # 保底：不让异常逃逸出 endpoint
        logger.exception("WS endpoint fatal: session_id=%s err=%s", session_id, e)
        try:
            await websocket.send_json(
                make_msg("error", session_id, code="FATAL", message=str(e))
            )
        except Exception:  # noqa: BLE001
            pass
    finally:
        # Always release the registry slot — works for normal disconnects,
        # fatal exceptions, and even client-side abrupt drops.
        get_connection_registry().unregister(session_id)
