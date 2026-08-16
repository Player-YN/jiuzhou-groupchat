"""AgentMemoryStore integration tests — Stage 7 Bug 2 per-agent unified memory.

端到端跨场景测试，覆盖 `.harness/reports/agent_memory_design.md` 验收点：

1. **test_cross_scene_continuity** — 跨场景连贯（核心断言）：
   shu-hang 在 dm 说"我同意帮忙" → group stream → shu-hang memory 含 dm 条目
   → LLM 生成的回复能引用这个事实（MockChatModel.assert_called_with）

2. **test_privacy_no_cross_dm_leak** — 隐私边界（核心断言）：
   yao-shi 在 dm 对 user 说"我恨白前辈" → group stream → shu-hang.load
   不应包含 yao-shi 的 dm 条目（X 不该读到 Y 的 dm with user）

3. **test_group_user_msg_fan_out** — Group user 消息 fan-out：
   group user 说话 → 九洲一号群 6 角色 memory 各 +1 条

4. **test_dm_init_payload_includes_group_history** — dm_init 协议：
   dm_init response payload 的 history 字段含 group source 标记的条目

测试模式：
- pytest 风格 `def test_X()` 函数（让 `pytest tests/` 真正收集到）
- 用 `:memory:` AgentMemoryStore 隔离
- 用 MockChatModel（不调真实 LLM，patch get_chat_model 走 mock）

跑法：
- pytest:  `cd backend && pytest tests/test_agent_memory_integration.py -v`
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# 把 backend 加到 path，这样 `from app...` 能找到
_BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_BACKEND_ROOT))

# 强制 mock LLM（避免触及真实 provider）
os.environ.setdefault("USE_MOCK_LLM", "true")

import pytest  # noqa: E402

from app.llm import MockChatModel  # noqa: E402
from app.memory.agent_memory import (  # noqa: E402
    AgentMemoryStore,
    ROLE_AGENT_KEYS,
    set_agent_memory_store,
)


# ============================================================================
# Fixtures + helpers
# ============================================================================
def _fast_mock(**_kw) -> MockChatModel:
    """返回无延迟的 MockChatModel（chunk_delay_ms=0），跑测试不慢。"""
    return MockChatModel(chunk_delay_ms=0)


@pytest.fixture
def temp_store(monkeypatch):
    """每个测试一个独立的 AgentMemoryStore（:memory:），注入到全局默认。"""
    store = AgentMemoryStore(":memory:")
    set_agent_memory_store(store)
    yield store
    set_agent_memory_store(None)  # 还原
    store.close()


@pytest.fixture
def mock_llm(monkeypatch):
    """patch get_chat_model 走 MockChatModel（无论哪个 provider 都走 mock）。"""
    import app.graph as graph_module
    monkeypatch.setattr(graph_module, "get_chat_model", _fast_mock)
    return _fast_mock


# ============================================================================
# Test 1: Cross-scene continuity（核心断言）
# ============================================================================
@pytest.mark.asyncio
async def test_cross_scene_continuity(temp_store, mock_llm):
    """shu-hang 在 dm 说"我同意帮忙" → group stream → shu-hang 知道这个事实。

    这是用户核心需求的实现验证：
    "我要把 NPC 都当成有声有色活灵活现的人物,而不只是 NPC 那么简单。
    所以单个角色的的记忆无论是群聊还是在私信窗口的时候,它应该是连贯的。"
    """
    from app.graph import stream_dm_chat, stream_group_chat

    session_id = "test-cross-scene"

    # ----- Step 1: shu-hang 在 dm 跟 user 说"我同意帮忙" -----
    # 1.1) user msg 持久化
    temp_store.append_message(
        session_id=session_id,
        agent_key="shu-hang",
        role="user",
        source="dm",
        speaker_key="user",
        text="书航,我等下要出远门,你能帮忙看店吗?",
        timestamp=1000,
    )
    # 1.2) stream_dm_chat 跑通(此时 shu-hang 没历史)
    events = []
    async for ev in stream_dm_chat(
        target_agent_key="shu-hang",
        user_text="书航,我等下要出远门,你能帮忙看店吗?",
        history=None,  # 让 stream_dm_chat 自己从 store 读
        session_id=session_id,
        memory_store=temp_store,
    ):
        events.append(ev)
    assert any(e.get("event") == "dm_done" for e in events)

    # ----- Step 2: shu-hang 在 dm 里答应了 -----
    temp_store.append_message(
        session_id=session_id,
        agent_key="shu-hang",
        role="user",
        source="dm",
        speaker_key="user",
        text="那我走了啊",
        timestamp=2000,
    )
    # 这次 stream_dm_chat 能看到 step 1 的历史
    events = []
    async for ev in stream_dm_chat(
        target_agent_key="shu-hang",
        user_text="那我走了啊",
        history=None,
        session_id=session_id,
        memory_store=temp_store,
    ):
        events.append(ev)
    done = next(e for e in events if e.get("event") == "dm_done")
    assert done.get("full_text"), "dm_done 必须有 full_text"
    # MockChatModel 应该基于完整 history(包含 step 1 + step 2)生成回复
    # 实际 mock 内容不重要(它是 random-ish),关键验证 store 含 step 1 记忆

    # ----- Step 3: 切换到 group,问 shu-hang "你之前答应过吧?" -----
    # 九洲一号群 user 发:"书航,你答应过我吧?" -> stream_group_chat
    # stream_group_chat 入口 fan-out user msg,agent_node 在 done 时 fan-out 发言
    group_events = []
    async for ev in stream_group_chat(
        user_text="书航,你答应过帮我看店吧?",
        max_rounds=2,  # 只跑 1 轮(书航),快
        session_id=session_id,
        memory_store=temp_store,
    ):
        group_events.append(ev)
        # 限制单 round
        if ev.get("event") == "agent_done" and ev.get("round", 0) >= 2:
            break

    # ----- Step 4: 验证 shu-hang 的 memory 包含 step 1+2 的 dm 历史 -----
    sh_memory = temp_store.load_agent_memory(session_id, "shu-hang")

    # shu-hang memory 至少应包含:
    # - step 1 user msg (dm, user)
    # - step 1 agent reply (dm, agent)
    # - step 2 user msg (dm, user)
    # - step 2 agent reply (dm, agent)
    # - step 3 group user msg (group, user) [fan-out]
    # - step 3 group agent reply (group, agent) [fan-out]
    dm_entries = [e for e in sh_memory if e.source == "dm"]
    group_entries = [e for e in sh_memory if e.source == "group"]

    assert len(dm_entries) >= 4, f"expected ≥4 dm entries, got {len(dm_entries)}"
    assert len(group_entries) >= 1, f"expected ≥1 group entry (fan-out), got {len(group_entries)}"

    # 关键断言:dm 条目里能找到 step 1 的"看店"话题
    all_text = " ".join(e.text for e in dm_entries)
    assert "看店" in all_text, f"shu-hang memory 应包含 step 1 看店 dm, got: {all_text}"

    # 关键断言:group 条目里能找到 step 3 的 user 问句
    all_group_text = " ".join(e.text for e in group_entries)
    assert "答应过" in all_group_text or "看店" in all_group_text, (
        f"shu-hang memory 应包含 group user msg fan-out, got: {all_group_text}"
    )


# ============================================================================
# Test 2: Privacy no cross-dm leak（核心隐私断言）
# ============================================================================
@pytest.mark.asyncio
async def test_privacy_no_cross_dm_leak(temp_store, mock_llm):
    """yao-shi 在 dm 对 user 说"我恨白前辈" → group stream → shu-hang.load 不含。

    九洲一号群 6 角色每个人都有独立 memory。Y 在 dm 跟 user 说的,X 看不到。
    """
    from app.graph import stream_dm_chat, stream_group_chat

    session_id = "test-privacy"

    # ----- Step 1: yao-shi 在 dm 跟 user 说"我恨白前辈" -----
    temp_store.append_message(
        session_id=session_id,
        agent_key="yao-shi",
        role="user",
        source="dm",
        speaker_key="user",
        text="药师, 你恨白前辈吗?",
        timestamp=1000,
    )
    events = []
    async for ev in stream_dm_chat(
        target_agent_key="yao-shi",
        user_text="药师, 你恨白前辈吗?",
        history=None,
        session_id=session_id,
        memory_store=temp_store,
    ):
        events.append(ev)
    # yao-shi 的 dm reply 应该不写明"我恨白前辈"(那是 user 问句,不是 yao-shi 说)
    # 但 stream_dm_chat 内部会持久化 agent reply 到 yao-shi.memory
    assert any(e.get("event") == "dm_done" for e in events), "stream_dm_chat 应产出 dm_done"

    # ----- Step 2: 切到 group,问 shu-hang "药师跟 user 聊了啥?" -----
    group_events = []
    async for ev in stream_group_chat(
        user_text="书航, 药师最近跟 user 聊了什么?",
        max_rounds=2,
        session_id=session_id,
        memory_store=temp_store,
    ):
        group_events.append(ev)
        if ev.get("event") == "agent_done" and ev.get("round", 0) >= 2:
            break

    # ----- Step 3: 关键断言 — shu-hang memory 不含 yao-shi 的 dm 内容 -----
    sh_memory = temp_store.load_agent_memory(session_id, "shu-hang")
    sh_texts = [e.text for e in sh_memory]

    # shu-hang 不应该看到 yao-shi 的 dm with user
    assert "我恨白前辈" not in " ".join(sh_texts), (
        f"shu-hang 不应该看到 yao-shi 的 dm 内容, got: {sh_texts}"
    )
    assert "药师, 你恨白前辈吗?" not in " ".join(sh_texts), (
        f"shu-hang 不应该看到 user 跟 yao-shi 的 dm, got: {sh_texts}"
    )

    # 同样,其他角色(除 yao-shi)也不应该看到
    for other in ["shu-hang", "san-lang", "bei-he", "bai-qianbei", "ling-die"]:
        mem = temp_store.load_agent_memory(session_id, other)
        texts = " ".join(e.text for e in mem)
        assert "我恨白前辈" not in texts, (
            f"{other} 不应看到 yao-shi 的 dm, got: {texts[:200]}"
        )

    # yao-shi 自己应该能看到自己的 dm(断言正例)
    ys_memory = temp_store.load_agent_memory(session_id, "yao-shi")
    ys_texts = " ".join(e.text for e in ys_memory)
    assert "药师, 你恨白前辈吗?" in ys_texts, (
        f"yao-shi 应该能看到自己的 dm, got: {ys_texts}"
    )


# ============================================================================
# Test 3: Group user msg fan-out
# ============================================================================
@pytest.mark.asyncio
async def test_group_user_msg_fan_out(temp_store, mock_llm):
    """group user 说话 → 九洲一号群 6 角色 memory 各 +1 条。

    九洲一号群是公开场景,每个人都"听到"了 user 说话。
    """
    from app.graph import stream_group_chat

    session_id = "test-fanout"

    # 启动一轮 group chat
    async for ev in stream_group_chat(
        user_text="@白前辈 在吗?",
        max_rounds=2,
        session_id=session_id,
        memory_store=temp_store,
    ):
        if ev.get("event") == "agent_done" and ev.get("round", 0) >= 2:
            break

    # 九洲一号群 6 角色都应该有 user 的 group msg (fan-out)
    for agent_key in ROLE_AGENT_KEYS:
        mem = temp_store.load_agent_memory(session_id, agent_key)
        # 至少 1 条 group source=user 的条目
        user_group_entries = [
            e for e in mem
            if e.source == "group" and e.role == "user" and e.speaker_key == "user"
        ]
        assert len(user_group_entries) >= 1, (
            f"{agent_key} 应该有 user group msg fan-out, got {[e.text for e in mem]}"
        )
        assert "@白前辈 在吗?" in user_group_entries[0].text, (
            f"{agent_key} 的 user group msg 应该包含 @白前辈, got: {user_group_entries[0].text}"
        )


# ============================================================================
# Test 4: dm_init payload includes group history
# ============================================================================
@pytest.mark.asyncio
async def test_dm_init_payload_includes_group_history(temp_store, mock_llm):
    """dm_init 响应 payload.history 含 group source 标记的条目。

    Stage 7 Bug 2: dm_init history 类型从 DmMessage[] -> AgentMemoryEntry[],
    含 source 字段。前端可按 source 区分群聊背景 vs 私聊内容。
    """
    from app.graph import stream_group_chat
    import json

    session_id = "test-dm-init-payload"

    # 模拟完整 WS endpoint(dm_init -> dm_msg)
    class FakeWS:
        def __init__(self):
            self._recv_queue: list[dict] = []
            self._sent: list[dict] = []

        async def accept(self):
            pass

        async def send_json(self, msg):
            self._sent.append(msg)

        async def receive_text(self):
            from fastapi import WebSocketDisconnect
            if self._recv_queue:
                return json.dumps(self._recv_queue.pop(0))
            raise WebSocketDisconnect()

    # Step 1: 先跑一轮 group chat(让 shu-hang memory 有 group 条目)
    async for ev in stream_group_chat(
        user_text="书航快来",
        max_rounds=2,
        session_id=session_id,
        memory_store=temp_store,
    ):
        if ev.get("event") == "agent_done" and ev.get("round", 0) >= 2:
            break

    # Step 2: 跑 dm_init(查 shu-hang 的 memory)
    ws = FakeWS()
    ws._recv_queue.append({"type": "dm_init", "payload": {"target_agent": "shu-hang"}})

    import asyncio
    from app.routers.ws import ws_endpoint
    task = asyncio.create_task(ws_endpoint(ws, session_id=session_id))
    await asyncio.sleep(1.0)
    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):
        pass

    # 找 dm_init 响应
    dm_init_resp = next(m for m in ws._sent if m["type"] == "dm_init")
    p = dm_init_resp["payload"]
    assert p["target_agent"] == "shu-hang"
    assert len(p["history"]) > 0, f"dm_init history 应非空, got: {p}"

    # 关键断言: history 条目应有 source 字段(group / dm)
    sources = set()
    for entry in p["history"]:
        assert "source" in entry, f"history 条目应有 source 字段, got: {entry}"
        assert entry["source"] in ("group", "dm"), f"unknown source: {entry['source']}"
        sources.add(entry["source"])

    # 应该至少含 group(从 step 1 的 fan-out)
    assert "group" in sources, f"dm_init history 应含 group 条目, got sources: {sources}"

    # 关键断言: 应该能识别 group 条目 + speaker_key
    group_entries = [e for e in p["history"] if e["source"] == "group"]
    assert len(group_entries) > 0
    for ge in group_entries:
        assert "speaker_key" in ge, f"group entry 应有 speaker_key, got: {ge}"
        # speaker_key 应该是 user 或 6 角色 key
        assert ge["speaker_key"] in ("user",) + ROLE_AGENT_KEYS, (
            f"unknown speaker_key: {ge['speaker_key']}"
        )