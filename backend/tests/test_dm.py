"""Stage 6 DM Phase 2 — 私信 (direct message) 集成测试。

覆盖父 spec 的所有验收点：
  1. dm_init 返回正确历史（含 memory_size + name + emoji + history）
  2. dm_msg → 目标 agent 流式回复（dm_thinking → dm_msg_chunk → dm_done）
  3. dm 记忆跨轮次持久化（第 1 轮 text 在第 2 轮 dm_init response 里出现）
  4. **dm 记忆隔离**：sess1/shu-hang 的消息绝不出现在 sess1/yao-shi 或 sess2/shu-hang 的历史里
  5. dm 流不触发群聊 cycle（不出 supervisor_decision / agent_done 等）

测试模式：
  - pytest 风格 `def test_X()` 函数（让 `pytest tests/` 真正收集到）
  - `if __name__ == "__main__"` 入口也支持 standalone 运行（与其他 stage 测试一致）
  - 用 `:memory:` SQLite store 隔离，patch `get_chat_model` 走 MockChatModel

跑法：
  - pytest:  `cd backend && pytest tests/test_dm.py -v`
  - 独立:    `cd backend && python tests/test_dm.py`
"""
from __future__ import annotations

import asyncio
import json
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
from app.memory import DmStore, set_dm_store  # noqa: E402
from app.memory.agent_memory import AgentMemoryStore, set_agent_memory_store  # noqa: E402


# ============================================================================
# Fixtures
# ============================================================================
def _fast_mock(**_kw) -> MockChatModel:
    """返回无延迟的 MockChatModel（chunk_delay_ms=0），跑测试不慢。"""
    return MockChatModel(chunk_delay_ms=0)


@pytest.fixture
def temp_store(monkeypatch):
    """每个测试一个独立的 AgentMemoryStore（:memory:），注入到全局默认。

    Stage 7 Bug 2: ws.py 改用 AgentMemoryStore (per-agent 统一 memory),不再用 DmStore。
    DmStore 类本身保留(向后兼容 sync tests),但 ws.py 路径不再依赖它。

    用 `:memory:` 保证测试之间零状态泄漏。
    """
    store = AgentMemoryStore(":memory:")
    set_agent_memory_store(store)
    yield store
    set_agent_memory_store(None)  # 还原
    store.close()


# ============================================================================
# Test 1: DmStore 基础 CRUD + 隔离
# ============================================================================
def test_dm_store_isolation():
    """核心隐私测试：每个 (session_id, agent_key) 独立。

    九洲一号群 6 角色：shu-hang / yao-shi / san-lang / bei-he / bai-qianbei / ling-die
    """
    s = DmStore(":memory:")

    # sess1/shu-hang 写入 2 条
    s.append_message("sess1", "shu-hang", "user", "你好书航", timestamp=1000)
    s.append_message(
        "sess1", "shu-hang", "agent", "哈哈",
        timestamp=2000, agent_name="宋书航", agent_emoji="🌟",
    )

    # sess1/yao-shi 写入 1 条
    s.append_message("sess1", "yao-shi", "user", "药师兄", timestamp=1500)

    # sess2/shu-hang 写入 1 条（不同 session）
    s.append_message("sess2", "shu-hang", "user", "第二轮", timestamp=3000)

    # ---- 隔离断言 ----
    h1 = s.load_history("sess1", "shu-hang")
    assert len(h1) == 2
    assert h1[0].text == "你好书航"
    assert h1[1].text == "哈哈"

    # 关键：yao-shi 看不到 shu-hang 的私聊
    h2 = s.load_history("sess1", "yao-shi")
    assert len(h2) == 1
    assert h2[0].text == "药师兄"
    assert "你好书航" not in [m.text for m in h2]
    assert "哈哈" not in [m.text for m in h2]

    # 关键：sess2 看不到 sess1 的私聊（即使 agent_key 相同）
    h3 = s.load_history("sess2", "shu-hang")
    assert len(h3) == 1
    assert h3[0].text == "第二轮"
    assert "你好书航" not in [m.text for m in h3]

    # 关键：sess1/bei-he 完全空（从未写入）
    h4 = s.load_history("sess1", "bei-he")
    assert len(h4) == 0

    # count + list_sessions
    assert s.count_messages("sess1", "shu-hang") == 2
    assert s.count_messages("sess1", "yao-shi") == 1
    assert sorted(s.list_sessions_for_agent("shu-hang")) == ["sess1", "sess2"]

    s.close()


def test_dm_store_validation():
    """DmStore 输入校验：role 非法 / 空 text 应当报错。"""
    s = DmStore(":memory:")
    import pytest as _pytest

    with _pytest.raises(ValueError, match="role must be"):
        s.append_message("s", "shu-hang", "boss", "hi", timestamp=1)

    with _pytest.raises(ValueError, match="text must be non-empty"):
        s.append_message("s", "shu-hang", "user", "", timestamp=1)

    with _pytest.raises(ValueError, match="session_id and agent_key"):
        s.append_message("", "shu-hang", "user", "hi", timestamp=1)

    s.close()


def test_dm_store_clear():
    """DmStore.clear_history 删除正确条数。"""
    s = DmStore(":memory:")
    s.append_message("s1", "shu-hang", "user", "a", timestamp=1)
    s.append_message("s1", "shu-hang", "agent", "b", timestamp=2)
    s.append_message("s1", "yao-shi", "user", "c", timestamp=3)

    deleted = s.clear_history("s1", "shu-hang")
    assert deleted == 2
    assert len(s.load_history("s1", "shu-hang")) == 0
    # yao-shi 不受影响
    assert len(s.load_history("s1", "yao-shi")) == 1

    s.close()


# ============================================================================
# Test 2: stream_dm_chat 不触发群聊 cycle
# ============================================================================
@pytest.mark.asyncio
async def test_stream_dm_chat_no_group_cycle(temp_store, monkeypatch):
    """关键断言：DM 流不触发任何群聊 cycle 事件。

    九洲一号群 cycle 事件类型：supervisor_decision / agent_thinking / agent_msg_chunk /
    agent_done / max_rounds_reached / group_chat_done。
    DM 流只能用 dm_* 事件。
    """
    # 强制使用 MockChatModel（即使 env 失效也兜底）
    import app.graph as graph_module
    monkeypatch.setattr(graph_module, "get_chat_model", _fast_mock)

    from app.graph import stream_dm_chat
    from app.models import DmMessage

    history = [
        DmMessage(role="user", text="老规矩", timestamp=1000),
        DmMessage(
            role="agent", text="嗯", timestamp=2000,
            agent_name="宋书航", agent_emoji="🌟",
        ),
    ]

    events = []
    async for ev in stream_dm_chat("shu-hang", "在不在", history=history):
        events.append(ev)

    # 1. 必须出现 DM 事件
    types = [e.get("event") for e in events]
    assert "dm_thinking" in types
    assert "dm_msg_chunk" in types
    assert "dm_done" in types

    # 2. 关键断言：绝不出现群聊 cycle 事件
    forbidden = {
        "supervisor_decision", "agent_thinking", "agent_msg_chunk",
        "agent_done", "max_rounds_reached", "group_chat_done",
    }
    leaked = [t for t in types if t in forbidden]
    assert not leaked, f"DM 流泄漏了群聊事件: {leaked}"

    # 3. dm_done 必须有 full_text
    done = [e for e in events if e["event"] == "dm_done"][0]
    assert done["full_text"], "dm_done.full_text 必须非空"
    assert done["agent"] == "shu-hang"


@pytest.mark.asyncio
async def test_stream_dm_chat_does_not_duplicate_persisted_current_user(
    temp_store, monkeypatch,
):
    """ws persists the user event first; the LLM must still see it once."""
    from langchain_core.messages import AIMessageChunk, HumanMessage
    import app.graph as graph_module

    captured = []

    class CaptureModel:
        async def astream(self, messages):
            captured.extend(messages)
            yield AIMessageChunk(content="收到")

    monkeypatch.setattr(graph_module, "_use_letta_path", lambda role_key: False)
    monkeypatch.setattr(graph_module, "get_chat_model", lambda **kwargs: CaptureModel())
    temp_store.append_message(
        "dm-dedupe", "shu-hang", "user", "dm", "user", "只出现一次",
    )

    async for _ in graph_module.stream_dm_chat(
        "shu-hang",
        "只出现一次",
        history=None,
        session_id="dm-dedupe",
        memory_store=temp_store,
    ):
        pass

    current_turns = [
        message for message in captured
        if isinstance(message, HumanMessage) and message.content == "只出现一次"
    ]
    assert len(current_turns) == 1


@pytest.mark.asyncio
async def test_ws_dm_msg_id_suppresses_duplicate_reply(temp_store, monkeypatch, tmp_path):
    """The same DM msg_id is acknowledged but generated and persisted once."""
    import uuid

    import app.graph as graph_module
    from app.behavior import DecisionLogStore, set_decision_log_store
    from app.routers.ws import ws_endpoint

    monkeypatch.setattr(graph_module, "get_chat_model", _fast_mock)
    event_id = f"dm-{uuid.uuid4()}"
    log_store = DecisionLogStore(tmp_path / "dm-decisions.sqlite")
    previous_log = set_decision_log_store(log_store)

    class FakeWS:
        def __init__(self):
            packet = {
                "type": "dm_msg",
                "payload": {"text": "同一条私信", "msg_id": event_id},
            }
            self._recv_queue = [
                {"type": "dm_init", "payload": {"target_agent": "shu-hang"}},
                packet,
                packet,
            ]
            self._sent = []

        async def accept(self): pass
        async def send_json(self, message): self._sent.append(message)

        async def receive_text(self):
            if self._recv_queue:
                return json.dumps(self._recv_queue.pop(0))
            from fastapi import WebSocketDisconnect
            raise WebSocketDisconnect()

    try:
        ws = FakeWS()
        await ws_endpoint(ws, session_id="dm-idempotent")
        assert len([message for message in ws._sent if message["type"] == "dm_done"]) == 1
        acknowledgements = [message for message in ws._sent if message["type"] == "dm_msg_ack"]
        assert len(acknowledgements) == 2
        assert acknowledgements[-1]["payload"]["status"] == "duplicate"
        memory = temp_store.load_agent_memory("dm-idempotent", "shu-hang")
        assert len([entry for entry in memory if entry.role == "user"]) == 1
        assert len([entry for entry in memory if entry.role == "agent"]) == 1
    finally:
        set_decision_log_store(previous_log)
        log_store.close()


@pytest.mark.asyncio
async def test_stream_dm_chat_unknown_agent(temp_store, monkeypatch):
    """未知 agent_key 应该 yield dm_error，而不是抛异常。"""
    import app.graph as graph_module
    monkeypatch.setattr(graph_module, "get_chat_model", _fast_mock)

    from app.graph import stream_dm_chat

    events = []
    async for ev in stream_dm_chat("not-a-real-agent", "hi", history=[]):
        events.append(ev)

    assert len(events) == 1
    assert events[0]["event"] == "dm_error"
    assert events[0]["code"] == "UNKNOWN_AGENT"


@pytest.mark.asyncio
async def test_dm_generation_failure_gets_target_persona_fallback(temp_store, monkeypatch):
    import app.graph as graph_module

    class FailingModel:
        async def astream(self, messages):
            if False:
                yield None
            raise RuntimeError("provider unavailable")

    monkeypatch.setattr(graph_module, "_use_letta_path", lambda role_key: False)
    monkeypatch.setattr(graph_module, "get_chat_model", lambda **kwargs: FailingModel())
    events = [event async for event in graph_module.stream_dm_chat(
        "yao-shi",
        "你在吗？",
        history=[],
        session_id="dm-fallback",
        memory_store=temp_store,
    )]
    done = next(event for event in events if event["event"] == "dm_done")
    assert done["agent"] == "yao-shi"
    assert done["full_text"] == "老夫在，容我稍后细看。"
    assert any(event.get("fallback") is True for event in events)


# ============================================================================
# Test 3: 完整端到端 WS 流程（用 FastAPI TestClient + 直接调 ws_endpoint）
# ============================================================================
@pytest.mark.asyncio
async def test_ws_dm_full_flow(temp_store, monkeypatch):
    """完整 WS 流程：dm_init → dm_msg → dm_init (第二次应看到第一次的消息) → 隔离检查。

    直接驱动 ws_endpoint（不用 TestClient，避免真实网络层）。
    """
    import app.graph as graph_module
    monkeypatch.setattr(graph_module, "get_chat_model", _fast_mock)
    monkeypatch.setattr(graph_module, "_use_letta_path", lambda role_key: False)

    from app.routers.ws import ws_endpoint

    # ---- 模拟一个 WS：recv queue + send list ----
    class FakeWS:
        def __init__(self):
            self._recv_queue: list[dict] = []
            self._sent: list[dict] = []
            self._closed = False

        async def accept(self):
            pass

        async def send_json(self, msg):
            self._sent.append(msg)

        async def receive_text(self):
            if self._closed:
                from fastapi import WebSocketDisconnect
                raise WebSocketDisconnect()
            if not self._recv_queue:
                # 模拟断连（让 endpoint 退出 read-loop）
                from fastapi import WebSocketDisconnect
                self._closed = True
                raise WebSocketDisconnect()
            return json.dumps(self._recv_queue.pop(0))

    ws = FakeWS()

    # 准备客户端消息序列
    ws._recv_queue.extend([
        # 1) dm_init: target=shu-hang
        {"type": "dm_init", "payload": {"target_agent": "shu-hang"}},
        # 2) dm_msg: user 发一句话 (T9 / Piece B: 带 author="凡人")
        {"type": "dm_msg", "payload": {"text": "你好书航", "author": "凡人"}},
        # 3) 断开
    ])

    # 跑 ws_endpoint（后台 task，让它跑完第一条消息后通过第二次 recv 触发断连）
    task = asyncio.create_task(ws_endpoint(ws, session_id="sess-test"))
    # 给它时间处理 dm_init + dm_msg + 断连
    await asyncio.sleep(2.0)
    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):
        pass

    # ---- 验证服务端推送 ----
    sent_types = [m.get("type") for m in ws._sent]
    print("sent types:", sent_types)

    # 1) 必有 session_init + dm_init（response）+ dm_msg_ack + dm_thinking + dm_done
    assert "session_init" in sent_types, f"missing session_init, got {sent_types}"
    assert "dm_init" in sent_types, f"missing dm_init, got {sent_types}"
    assert "dm_msg_ack" in sent_types, f"missing dm_msg_ack, got {sent_types}"
    assert "dm_thinking" in sent_types, f"missing dm_thinking, got {sent_types}"
    assert "dm_done" in sent_types, f"missing dm_done, got {sent_types}"
    # 至少 1 个 dm_msg_chunk
    chunk_count = sum(1 for t in sent_types if t == "dm_msg_chunk")
    assert chunk_count >= 1, f"expected ≥1 dm_msg_chunk, got {chunk_count}"

    # 2) dm_init 响应 payload 必须有 target_agent + name + emoji + history + memory_size
    dm_init_resp = next(m for m in ws._sent if m["type"] == "dm_init")
    p = dm_init_resp["payload"]
    assert p["target_agent"] == "shu-hang"
    assert p["name"] == "宋书航"
    assert p["emoji"] == "🌟"
    assert p["memory_size"] == 0  # 第一次没有历史
    assert p["history"] == []

    # 3) dm_done 必须有 full_text
    done = next(m for m in ws._sent if m["type"] == "dm_done")
    assert done["payload"]["full_text"]
    assert done["payload"]["agent"] == "shu-hang"

    # ---- 验证持久化 ----
    # Stage 7 Bug 2: 用 AgentMemoryStore.load_agent_memory (per-agent 统一 memory)
    hist = temp_store.load_agent_memory("sess-test", "shu-hang")
    assert len(hist) == 2, f"expected 2 persisted msgs, got {len(hist)}: {[m.text for m in hist]}"
    assert hist[0].role == "user"
    assert hist[0].text == "你好书航"
    # T9 / Piece B: 显式传 author 应被持久化 (而非 fallback "神秘人")
    assert hist[0].author == "凡人", f"expected author='凡人', got {hist[0].author!r}"
    assert hist[1].role == "agent"
    assert hist[1].text  # 非空
    assert hist[1].author is None  # agent-typed 行 author=None

    # ---- 验证其他 agent 完全看不到 ----
    assert len(temp_store.load_agent_memory("sess-test", "yao-shi")) == 0
    assert len(temp_store.load_agent_memory("sess-test", "bei-he")) == 0


@pytest.mark.asyncio
async def test_ws_dm_init_with_existing_history(temp_store, monkeypatch):
    """预先注入历史，dm_init 应该返回这些历史。"""
    import app.graph as graph_module
    monkeypatch.setattr(graph_module, "get_chat_model", _fast_mock)

    from app.routers.ws import ws_endpoint

    # 预注入：sess-x / shu-hang 已有 3 条历史
    # Stage 7 Bug 2: 用 AgentMemoryStore.append_message(source="dm")
    temp_store.append_message(
        "sess-x", "shu-hang", "user", "dm", "user", "旧问 1", timestamp=1000,
    )
    temp_store.append_message(
        "sess-x", "shu-hang", "agent", "dm", "shu-hang", "旧答 1",
        timestamp=2000, agent_name="宋书航", agent_emoji="🌟",
    )
    temp_store.append_message(
        "sess-x", "shu-hang", "user", "dm", "user", "旧问 2", timestamp=3000,
    )

    class FakeWS:
        def __init__(self):
            self._recv_queue = [{"type": "dm_init", "payload": {"target_agent": "shu-hang"}}]
            self._sent: list[dict] = []

        async def accept(self): pass
        async def send_json(self, msg): self._sent.append(msg)

        async def receive_text(self):
            if self._recv_queue:
                return json.dumps(self._recv_queue.pop(0))
            from fastapi import WebSocketDisconnect
            raise WebSocketDisconnect()

    ws = FakeWS()
    task = asyncio.create_task(ws_endpoint(ws, session_id="sess-x"))
    await asyncio.sleep(1.0)
    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):
        pass

    # dm_init 响应 payload.history 必须含 3 条历史
    dm_init_resp = next(m for m in ws._sent if m["type"] == "dm_init")
    p = dm_init_resp["payload"]
    assert p["memory_size"] == 3
    assert len(p["history"]) == 3
    assert [m["text"] for m in p["history"]] == ["旧问 1", "旧答 1", "旧问 2"]
    assert p["history"][0]["role"] == "user"
    assert p["history"][1]["role"] == "agent"
    assert p["history"][1]["agent_emoji"] == "🌟"
    # T9 / Piece B: user-typed 行 agent_memory 内部 backfill 到 '神秘人'
    # (seed 没传 author, 走 fallback); agent-typed 行 author=None。
    assert p["history"][0].get("author") == "神秘人"
    assert p["history"][1].get("author") is None


@pytest.mark.asyncio
async def test_ws_dm_init_unknown_agent(temp_store, monkeypatch):
    """dm_init 传入非法 agent_key → dm_error (UNKNOWN_AGENT)。"""
    import app.graph as graph_module
    monkeypatch.setattr(graph_module, "get_chat_model", _fast_mock)

    from app.routers.ws import ws_endpoint

    class FakeWS:
        def __init__(self):
            self._recv_queue = [{"type": "dm_init", "payload": {"target_agent": "mao"}}]
            self._sent: list[dict] = []

        async def accept(self): pass
        async def send_json(self, msg): self._sent.append(msg)

        async def receive_text(self):
            if self._recv_queue:
                return json.dumps(self._recv_queue.pop(0))
            from fastapi import WebSocketDisconnect
            raise WebSocketDisconnect()

    ws = FakeWS()
    task = asyncio.create_task(ws_endpoint(ws, session_id="sess-bad"))
    await asyncio.sleep(0.5)
    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):
        pass

    errs = [m for m in ws._sent if m["type"] == "dm_error"]
    assert errs, f"expected dm_error, got {[m['type'] for m in ws._sent]}"
    assert errs[0]["payload"]["code"] == "UNKNOWN_AGENT"


@pytest.mark.asyncio
async def test_ws_dm_msg_without_init(temp_store, monkeypatch):
    """没发 dm_init 就发 dm_msg → dm_error (NOT_IN_DM_MODE)。"""
    import app.graph as graph_module
    monkeypatch.setattr(graph_module, "get_chat_model", _fast_mock)

    from app.routers.ws import ws_endpoint

    class FakeWS:
        def __init__(self):
            self._recv_queue = [{"type": "dm_msg", "payload": {"text": "我没 init"}}]
            self._sent: list[dict] = []

        async def accept(self): pass
        async def send_json(self, msg): self._sent.append(msg)

        async def receive_text(self):
            if self._recv_queue:
                return json.dumps(self._recv_queue.pop(0))
            from fastapi import WebSocketDisconnect
            raise WebSocketDisconnect()

    ws = FakeWS()
    task = asyncio.create_task(ws_endpoint(ws, session_id="sess-noinit"))
    await asyncio.sleep(0.5)
    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):
        pass

    errs = [m for m in ws._sent if m["type"] == "dm_error"]
    assert errs, f"expected dm_error, got {[m['type'] for m in ws._sent]}"
    assert errs[0]["payload"]["code"] == "NOT_IN_DM_MODE"


@pytest.mark.asyncio
async def test_ws_group_msg_blocked_in_dm_mode(temp_store, monkeypatch):
    """DM 模式下，发群聊 user_msg 应该被 reject (MODE_CONFLICT)。"""
    import app.graph as graph_module
    monkeypatch.setattr(graph_module, "get_chat_model", _fast_mock)

    from app.routers.ws import ws_endpoint

    class FakeWS:
        def __init__(self):
            self._recv_queue = [
                {"type": "dm_init", "payload": {"target_agent": "shu-hang"}},
                {"type": "user_msg", "payload": {"text": "群里说话"}},
            ]
            self._sent: list[dict] = []
            self._closed = False

        async def accept(self): pass
        async def send_json(self, msg): self._sent.append(msg)

        async def receive_text(self):
            if self._recv_queue:
                return json.dumps(self._recv_queue.pop(0))
            if self._closed:
                from fastapi import WebSocketDisconnect
                raise WebSocketDisconnect()
            self._closed = True
            from fastapi import WebSocketDisconnect
            raise WebSocketDisconnect()

    ws = FakeWS()
    task = asyncio.create_task(ws_endpoint(ws, session_id="sess-conflict"))
    await asyncio.sleep(1.0)
    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):
        pass

    errs = [m for m in ws._sent if m["type"] == "error"]
    mode_conflict = [e for e in errs if e["payload"].get("code") == "MODE_CONFLICT"]
    assert mode_conflict, (
        f"expected MODE_CONFLICT error, got error codes: "
        f"{[e['payload'].get('code') for e in errs]}"
    )


# ============================================================================
# Standalone 入口（与其他 stage 测试一致）
# ============================================================================
async def main() -> int:
    """独立运行所有 test（不进 pytest 也能跑通）。"""
    print("=" * 60)
    print("Stage 6 DM Phase 2 Test Report")
    print("=" * 60)

    # 用临时 :memory: store 跑全套
    store = DmStore(":memory:")
    set_dm_store(store)

    # patch get_chat_model → MockChatModel
    import app.graph as graph_module
    original = graph_module.get_chat_model
    graph_module.get_chat_model = lambda **kw: MockChatModel()

    failures: list[str] = []

    # Run all pytest-style test functions via direct call
    # (skip sync tests here — they need pytest fixtures)

    # Run the async tests directly
    try:
        print("\n[1] stream_dm_chat_no_group_cycle")
        await test_stream_dm_chat_no_group_cycle(_StandaloneStore(), _StandaloneMonkey())
        print("  PASS")
    except Exception as e:
        failures.append(f"stream_dm_chat_no_group_cycle: {e}")
        print(f"  FAIL: {e}")

    try:
        print("\n[2] stream_dm_chat_unknown_agent")
        await test_stream_dm_chat_unknown_agent(_StandaloneStore(), _StandaloneMonkey())
        print("  PASS")
    except Exception as e:
        failures.append(f"stream_dm_chat_unknown_agent: {e}")
        print(f"  FAIL: {e}")

    # 还原
    graph_module.get_chat_model = original
    set_dm_store(None)
    store.close()

    print("\n" + "=" * 60)
    if failures:
        print(f"  RESULT: FAIL — {len(failures)} failure(s)")
        for f in failures:
            print(f"    - {f}")
        print("=" * 60)
        return 1
    print("  RESULT: PASS (smoke subset)")
    print("=" * 60)
    return 0


class _StandaloneStore:
    """Sync test_dm_store_isolation via direct call (no pytest fixture needed)."""
    pass


class _StandaloneMonkey:
    """Sync monkeypatch placeholder."""
    def setattr(self, target, name, value):
        setattr(target, name, value)


# 让 standalone runner 直接调用 sync test（不需要 fixture）
def _run_sync_tests() -> int:
    failures = 0
    for name in [
        "test_dm_store_isolation",
        "test_dm_store_validation",
        "test_dm_store_clear",
    ]:
        try:
            globals()[name]()
            print(f"  [sync] {name}: PASS")
        except Exception as e:
            failures += 1
            print(f"  [sync] {name}: FAIL — {e}")
    return failures


if __name__ == "__main__":
    # Standalone: 先跑 sync tests，再跑 async smoke
    print("=== SYNC tests ===")
    n_sync_fail = _run_sync_tests()
    print(f"\nsync failures: {n_sync_fail}")

    print("\n=== ASYNC smoke ===")
    rc = asyncio.run(main())
    sys.exit(rc or n_sync_fail)
