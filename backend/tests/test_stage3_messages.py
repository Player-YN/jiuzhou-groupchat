"""Stage 3 smoke test: 验证 add_messages reducer 让 yao-shi agent 看到 shu-hang 回复。

Stage 4-B 九洲一号群聊天群 6-Agent Group Chat 的核心契约：shu-hang (宋书航) 回复后,
yao-shi (药师) agent 调 LLM 时, 它的 messages 列表里必须包含 shu-hang 的 assistant
消息 (add_messages reducer 的责任)。

本测试:
  1. patch `app.llm.get_chat_model` 返回 RecordingMockChatModel (记录每次 LLM 调用的 messages)
  2. 跑 stream_group_chat, max_rounds=3 → supervisor 调度 shu-hang → yao-shi → san-lang
  3. 检查第 2 次 LLM 调用 (yao-shi) 的 messages 列表是否包含 shu-hang 的回复
  4. 失败 → exit 1, 成功 → exit 0

跑法 (后端目录): `python tests/test_stage3_messages.py`
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import AsyncIterator

# 把 backend 加到 path, 这样 `from app...` 能找到
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from langchain_core.messages import AIMessageChunk, BaseMessage
from langchain_core.outputs import ChatGenerationChunk

from app.llm import MockChatModel
import app.llm as llm_module
import app.graph as graph_module


# =============================================================================
# Recording Mock — 每次 _astream 调用都把 messages 列表存到模块级变量
# (Pydantic v2 把 class-level 列表变 ModelPrivateAttr, 不能直接 append)
# =============================================================================
_RECORDINGS: list[list[BaseMessage]] = []


class RecordingMockChatModel(MockChatModel):
    """继承 MockChatModel, 额外把每次 LLM 调用的 messages 全部记录到模块级列表."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # 加速 mock, 不需要逐字 delay
        self.chunk_delay_ms = 0

    async def _astream(
        self, messages, stop=None, **kwargs
    ) -> AsyncIterator[ChatGenerationChunk]:
        # 关键: 记录这次 LLM 调用的 messages (浅拷贝足够)
        _RECORDINGS.append(list(messages))

        # 复用父类角色挑选逻辑
        reply_text = self.reply
        for m in messages:
            content = m.content if hasattr(m, "content") else str(m)
            for keyword, role_reply in self.ROLE_REPLIES.items():
                if keyword in content:
                    reply_text = role_reply
                    break
            if reply_text != self.reply:
                break

        for i, ch in enumerate(reply_text):
            yield ChatGenerationChunk(
                message=AIMessageChunk(content=ch, id=f"mock-{i}"),
                generation_info={"mock_chunk_index": i},
            )


def patched_get_chat_model(temperature: float = 0.7, provider: str | None = None):
    """替换 app.llm.get_chat_model / app.graph.get_chat_model.

    Stage 4-B: 接受 provider 参数 (per-role provider routing), 透传给 RecordingMock
    """
    return RecordingMockChatModel()


# shu-hang (宋书航) 角色的 Mock 回复, 来自 app/llm.py MockChatModel.ROLE_REPLIES
SHUHANG_REPLY_PREFIX = "🌟 妈耶！大家好我是宋书航"


def _msg_preview(m: BaseMessage, n: int = 100) -> str:
    """消息预览."""
    role = type(m).__name__
    content = m.content if hasattr(m, "content") else str(m)
    name = getattr(m, "name", "") or ""
    return f"{role}(name={name!r}): {content[:n]!r}"


async def main() -> int:
    # 清空 recording
    _RECORDINGS.clear()

    # patch get_chat_model (graph.py 在模块加载时 capture 了, 必须改 module 内的引用)
    original_in_llm = llm_module.get_chat_model
    original_in_graph = graph_module.get_chat_model
    llm_module.get_chat_model = patched_get_chat_model
    graph_module.get_chat_model = patched_get_chat_model

    print("[stage3] running stream_group_chat (mock LLM, max_rounds=3, 6 九洲一号群角色)...")
    user_text = "我们来讨论一下 AI 健身教练的可行性"
    events_seen: list[str] = []
    try:
        async for ev in graph_module.stream_group_chat(
            user_text, topic="AI 健身教练", max_rounds=3
        ):
            et = ev.get("event")
            events_seen.append(et or "?")
            if et == "agent_done":
                print(f"  [event] agent_done: {ev.get('name')} ({ev.get('agent')}) round={ev.get('round')}")
            elif et == "error":
                print(f"  [event] ERROR: {ev.get('message')}")
    finally:
        llm_module.get_chat_model = original_in_llm
        graph_module.get_chat_model = original_in_graph

    n_calls = len(_RECORDINGS)
    print(f"\n[stage3] recorded {n_calls} LLM call(s); events: {events_seen}")

    if n_calls < 2:
        print(f"[FAIL] 期望至少 2 次 LLM 调用 (shu-hang + yao-shi), 实际 {n_calls}")
        return 1

    # 1st call: shu-hang (宋书航) 调 LLM
    first_call = _RECORDINGS[0]
    print(f"\n[1st LLM call] shu-hang, {len(first_call)} messages:")
    for i, m in enumerate(first_call):
        print(f"  [{i}] {_msg_preview(m, 80)}")

    # 2nd call: yao-shi (药师) 调 LLM
    second_call = _RECORDINGS[1]
    print(f"\n[2nd LLM call] yao-shi, {len(second_call)} messages:")
    for i, m in enumerate(second_call):
        print(f"  [{i}] {_msg_preview(m, 120)}")

    # ---- 核心断言 ----
    # G1: 1st call 含 system msg (宋书航) + 至少 1 条 user msg
    has_shuhang_system = any(
        hasattr(m, "content") and "宋书航" in str(m.content) for m in first_call
    )
    has_user_in_first = any(
        type(m).__name__ == "HumanMessage" for m in first_call
    )

    # G2: 2nd call 含 system msg (药师) + HumanMessage + 至少 1 条 assistant 消息 (来自 shu-hang)
    has_yaoshi_system = any(
        hasattr(m, "content") and "药师" in str(m.content) for m in second_call
    )
    has_user_in_second = any(
        type(m).__name__ == "HumanMessage" for m in second_call
    )
    # 关键断言: yao-shi 的 LLM 看到了 shu-hang 的回复 (用 prefix 匹配即可, Mock 逐字输出)
    has_shuhang_reply_in_second = any(
        hasattr(m, "content") and SHUHANG_REPLY_PREFIX in str(m.content)
        for m in second_call
    )
    # 找一下 shu-hang 的 assistant 消息的 name 字段 (应该是 "宋书航")
    shuhang_msg_names = [
        getattr(m, "name", None) for m in second_call
        if hasattr(m, "content") and SHUHANG_REPLY_PREFIX in str(m.content)
    ]

    # G3: 消息顺序: yao-shi node 自己 prepend 了 system_msg, 顺序是 [yaoshi_system, ...history]
    yaoshi_system_at_first = (
        len(second_call) > 0
        and hasattr(second_call[0], "content")
        and "药师" in str(second_call[0].content)
    )

    # ---- Report ----
    print("\n" + "=" * 60)
    print("STAGE 3 Test Report — add_messages reducer visibility (Stage 4-B: 6 九洲一号群角色)")
    print("=" * 60)
    print(f"  G1-1 1st call has shu-hang system:    {'PASS' if has_shuhang_system else 'FAIL'}")
    print(f"  G1-2 1st call has user msg:           {'PASS' if has_user_in_first else 'FAIL'}")
    print(f"  G2-1 2nd call has yao-shi system:     {'PASS' if has_yaoshi_system else 'FAIL'}")
    print(f"  G2-2 2nd call has user msg:           {'PASS' if has_user_in_second else 'FAIL'}")
    print(f"  G2-3 2nd call has shu-hang reply:     {'PASS' if has_shuhang_reply_in_second else 'FAIL'}")
    print(f"  G2-4 2nd call yao-shi system @0:      {'PASS' if yaoshi_system_at_first else 'FAIL'}")
    print(f"       shu-hang reply msg name:         {shuhang_msg_names}")
    print(f"       Total LLM calls:                 {n_calls}")
    print(f"       Agent done events:               {[e for e in events_seen if e == 'agent_done']}")
    print("=" * 60)

    core_pass = (
        has_shuhang_system
        and has_user_in_first
        and has_yaoshi_system
        and has_user_in_second
        and has_shuhang_reply_in_second
    )
    if core_pass:
        print("  RESULT: PASS — add_messages reducer 让 yao-shi 看到了 shu-hang 的回复")
    else:
        print("  RESULT: FAIL — add_messages reducer 没把 shu-hang 的回复传给 yao-shi")
    print("=" * 60)

    return 0 if core_pass else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
