"""Stage 5-A smoke test: 验证 `_trim_messages` + `_summary_cache` 上下文管理。

Stage 5-A 九洲一号群上下文管理契约:
  - agent_node 在调 LLM 前必须经过 `_trim_messages` 处理 msgs
  - 滑动窗口保留最近 20 条完整消息
  - 早期消息调 minimax M3 摘要成 `system_context_summary` 放在 system 之后
  - `_summary_cache` 避免重复摘要
  - 总数 ≤ 22 (1 system + 1 summary + 20 recent)

测试流程:
  1. mock `get_chat_model` 返回 SummaryRecordingMock（识别摘要 prompt 和角色 prompt）
  2. clear `_summary_cache`
  3. 通过 `stream_group_chat` 注入 30 条 state.messages (29 history + 1 user_text)
  4. max_rounds=1 → supervisor 调度 shu-hang (九洲一号群主角, minimax M3) 一轮
  5. 抓取 LLM 实际收到的 messages 列表（首个角色 LLM 调用）
  6. 验证 6 项 PASS:
       G1: 第 1 条是 system_msg (宋书航 角色 system prompt)
       G2: 第 2 条是 system_context_summary (含 is_summary=True marker)
       G3: 总消息条数 ≤ 22
       G4: 完整保留最近 20 条 (索引 [2..21], 即原 state.messages[-20:])
       G5: 摘要缓存被填充 (size >= 1)
       G6: 摘要实际有文本 (非空, 非 "（早期...无有效摘要）" fallback)

跑法 (后端目录): `python tests/test_stage5_trim.py`
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import AsyncIterator

# 把 backend 加到 path, 这样 `from app...` 能找到
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    BaseMessage,
    HumanMessage,
    SystemMessage,
)
from langchain_core.outputs import ChatGenerationChunk

import app.graph as graph_module
from app.llm import MockChatModel


# =============================================================================
# SummaryRecordingMock — 识别两类 prompt:
#   1. 摘要 prompt (含 "请把以下早期群聊压缩" → 返回固定 SUMMARY_REPLY)
#   2. 角色 prompt (system 含 ROLE_REPLIES 关键词 → 返回角色 reply)
# =============================================================================
_RECORDINGS: list[list[BaseMessage]] = []
_SUMMARY_CALL_COUNT: list[int] = [0]
_ROLE_CALL_COUNT: list[int] = [0]


# 测试用的固定摘要文本 (Mock LLM 返回值, 模拟 M3 摘要)
SUMMARY_REPLY = "（Mock 摘要）九洲一号群讨论了一个话题,6 角色各自表态,关键决策:暂不行动。"


class SummaryRecordingMock(MockChatModel):
    """继承 MockChatModel,区分摘要 LLM 调用和角色 LLM 调用."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # 加速 mock,不需要逐字 delay
        self.chunk_delay_ms = 0

    async def _astream(
        self, messages, stop=None, **kwargs
    ) -> AsyncIterator[ChatGenerationChunk]:
        # 判断本次调用类型
        is_summary_call = False
        for m in messages:
            content_str = str(m.content if hasattr(m, "content") else str(m))
            if "请把以下早期群聊压缩" in content_str:
                is_summary_call = True
                break

        # 记录每次调用的 messages
        _RECORDINGS.append(list(messages))

        if is_summary_call:
            _SUMMARY_CALL_COUNT[0] += 1
            for i, ch in enumerate(SUMMARY_REPLY):
                yield ChatGenerationChunk(
                    message=AIMessageChunk(content=ch, id=f"summary-{i}"),
                    generation_info={"mock_chunk_index": i, "kind": "summary"},
                )
            return

        # 角色回复
        _ROLE_CALL_COUNT[0] += 1
        reply_text = self.reply
        for m in messages:
            content_str = str(m.content if hasattr(m, "content") else str(m))
            for keyword, role_reply in self.ROLE_REPLIES.items():
                if keyword in content_str:
                    reply_text = role_reply
                    break
            if reply_text != self.reply:
                break

        for i, ch in enumerate(reply_text):
            yield ChatGenerationChunk(
                message=AIMessageChunk(content=ch, id=f"role-{i}"),
                generation_info={"mock_chunk_index": i, "kind": "role"},
            )


def patched_get_chat_model(temperature: float = 0.7, provider: str | None = None):
    """替换 get_chat_model — 让所有调用都走 SummaryRecordingMock."""
    return SummaryRecordingMock()


def _msg_preview(m: BaseMessage, n: int = 70) -> str:
    role = type(m).__name__
    content = m.content if hasattr(m, "content") else str(m)
    name = getattr(m, "name", "") or ""
    is_summary = getattr(m, "additional_kwargs", {}).get("is_summary", False)
    marker = " [SUMMARY]" if is_summary else ""
    return f"{role}(name={name!r}{marker}): {content[:n]!r}"


async def main() -> int:
    # ---- 重置状态 ----
    _RECORDINGS.clear()
    _SUMMARY_CALL_COUNT[0] = 0
    _ROLE_CALL_COUNT[0] = 0
    graph_module.clear_summary_cache()

    # ---- patch get_chat_model ----
    original_get_chat_model = graph_module.get_chat_model
    graph_module.get_chat_model = patched_get_chat_model

    # ---- 构造 29 条 history + 1 条 user_text = 30 条 state.messages ----
    state_messages: list[BaseMessage] = []
    role_speakers = ["宋书航", "药师", "狂刀三浪", "北河散人", "白前辈", "灵蝶尊者"]
    for i in range(29):
        if i % 2 == 0:
            state_messages.append(
                HumanMessage(content=f"用户第 {i // 2 + 1} 轮问题:继续讨论九洲一号群话题 line {i}")
            )
        else:
            speaker = role_speakers[(i // 2) % len(role_speakers)]
            state_messages.append(
                AIMessage(
                    content=f"[{speaker}] 第 {i} 条回复 stub。",
                    name=speaker,
                )
            )

    assert len(state_messages) == 29

    user_text = "用户第 15 轮问题:继续九洲一号群话题"  # 第 30 条 → total 30
    expected_total_in_state = 30  # history(29) + user_text(1)

    state_messages_full = list(state_messages) + [HumanMessage(content=user_text)]
    expected_recent_in_state = state_messages_full[-20:]  # 最近 20 条

    print(f"[stage5a] state.messages = {expected_total_in_state} msgs")
    print("[stage5a]   (29 history + 1 user_text)")
    print("[stage5a] expected after trim: 1 system + 1 summary + 20 recent = 22")

    # ---- 通过 stream_group_chat 跑 agent_node (确保 LangGraph runnable context 正确) ----
    events_seen: list[str] = []
    try:
        async for ev in graph_module.stream_group_chat(
            user_text=user_text,
            topic="九洲一号群话题",
            history=state_messages,  # 29 history → agent_node 看到 30 条 (含自己加的 user_text)
            max_rounds=1,  # 只跑 1 轮 → supervisor → shu-hang 一次
        ):
            et = ev.get("event")
            events_seen.append(et or "?")
            if et == "agent_done":
                print(f"  [event] agent_done: {ev.get('name')} ({ev.get('agent')}) round={ev.get('round')}")
            elif et == "error":
                print(f"  [event] ERROR: {ev.get('message')}")
    finally:
        graph_module.get_chat_model = original_get_chat_model

    # ---- 检查 LLM 调用次数 ----
    n_calls = len(_RECORDINGS)
    print(f"\n[stage5a] LLM 调用次数: total={n_calls}, summary={_SUMMARY_CALL_COUNT[0]}, role={_ROLE_CALL_COUNT[0]}")

    if n_calls < 1:
        print(f"[FAIL] 期望至少 1 次 LLM 调用 (角色回复),实际 {n_calls}")
        return 1

    # ---- 找到那个角色 LLM 调用 (含 shu-hang role system_msg + 不含 summary prompt) ----
    # 关键: 角色调用的第一条是 SystemMessage (有"宋书航"角色 system + 没 is_summary marker);
    # 摘要调用的唯一一条是 HumanMessage (摘要 prompt, 含"请把以下早期群聊压缩")
    role_call_idx: int | None = None
    for idx, rec in enumerate(_RECORDINGS):
        has_role_system = any(
            isinstance(m, SystemMessage)
            and not getattr(m, "additional_kwargs", {}).get("is_summary", False)
            and "宋书航" in str(getattr(m, "content", ""))
            for m in rec
        )
        has_summary_prompt = any(
            "请把以下早期群聊压缩" in str(getattr(m, "content", ""))
            for m in rec
        )
        if has_role_system and not has_summary_prompt and role_call_idx is None:
            role_call_idx = idx

    if role_call_idx is None:
        print("[FAIL] 没找到角色 LLM 调用 (含 shu-hang system prompt 的那次)")
        return 1

    role_msgs = _RECORDINGS[role_call_idx]
    print(f"\n[stage5a] 角色 LLM 调用 #{role_call_idx}, {len(role_msgs)} msgs:")
    for i, m in enumerate(role_msgs):
        print(f"  [{i}] {_msg_preview(m, 60)}")

    # ============================================================
    # Gate 1: 第 0 条是 system_msg (角色 prompt: 宋书航)
    # ============================================================
    g1_pass = (
        len(role_msgs) >= 1
        and isinstance(role_msgs[0], SystemMessage)
        and "宋书航" in str(getattr(role_msgs[0], "content", ""))
    )

    # ============================================================
    # Gate 2: 第 1 条是 system_context_summary (含 is_summary marker)
    # ============================================================
    g2_pass = (
        len(role_msgs) >= 2
        and isinstance(role_msgs[1], SystemMessage)
        and getattr(role_msgs[1], "additional_kwargs", {}).get("is_summary", False) is True
        and "system_context_summary" in str(getattr(role_msgs[1], "content", ""))
    )

    # ============================================================
    # Gate 3: 总条数 ≤ 22 (1 system + 1 summary + 20 recent)
    # ============================================================
    g3_pass = len(role_msgs) <= 22

    # ============================================================
    # Gate 4: 最近 20 条完整保留 (索引 [2..21] = state.messages[-20:])
    # ============================================================
    actual_recent: list[BaseMessage] = role_msgs[2:]
    g4_pass = (
        len(actual_recent) == 20
        and all(
            actual_recent[i].content == expected_recent_in_state[i].content
            for i in range(20)
        )
        and all(
            getattr(actual_recent[i], "name", None) == getattr(expected_recent_in_state[i], "name", None)
            for i in range(20)
        )
    )

    # ============================================================
    # Gate 5: _summary_cache 被填充 (size >= 1)
    # ============================================================
    cache_stats = graph_module.get_summary_cache_stats()
    g5_pass = cache_stats.get("size", 0) >= 1

    # ============================================================
    # Gate 6: 摘要含非空文本 (非 fallback 占位 "无有效摘要")
    # ============================================================
    summary_text = str(getattr(role_msgs[1], "content", ""))
    g6_pass = (
        len(summary_text.strip()) > 0
        and "system_context_summary" in summary_text
        and "摘要失败" not in summary_text
        and "无有效摘要" not in summary_text
    )

    # ============================================================
    # Report
    # ============================================================
    print("\n" + "=" * 64)
    print("STAGE 5-A Test Report — `_trim_messages` + `_summary_cache` (九洲一号群)")
    print("=" * 64)
    print(f"  G1 [0]=role system (宋书航):              {'PASS' if g1_pass else 'FAIL'}")
    print(f"  G2 [1]=system_context_summary (marker):  {'PASS' if g2_pass else 'FAIL'}")
    print(f"  G3 total msgs <= 22:                     {'PASS' if g3_pass else 'FAIL'}  (actual {len(role_msgs)})")
    print(f"  G4 [2..21]=state.messages[-20:] 完整:     {'PASS' if g4_pass else 'FAIL'}")
    print(f"  G5 _summary_cache 被填充 (size>=1):      {'PASS' if g5_pass else 'FAIL'}  (size={cache_stats.get('size')})")
    print(f"  G6 摘要含非空文本 (非 fallback):          {'PASS' if g6_pass else 'FAIL'}")
    print(f"       LLM 调次数: total={n_calls}, summary={_SUMMARY_CALL_COUNT[0]}, role={_ROLE_CALL_COUNT[0]}")
    print(f"       Cache stats: {cache_stats}")
    print(f"       Events seen: {events_seen}")
    print("=" * 64)

    all_pass = all([g1_pass, g2_pass, g3_pass, g4_pass, g5_pass, g6_pass])
    if all_pass:
        print("  RESULT: PASS — Stage 5-A 上下文管理契约全部满足 (6/6)")
    else:
        fails = [n for n, p in zip(["G1", "G2", "G3", "G4", "G5", "G6"],
                                   [g1_pass, g2_pass, g3_pass, g4_pass, g5_pass, g6_pass]) if not p]
        print(f"  RESULT: FAIL — {', '.join(fails)} 没通过")
    print("=" * 64)

    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
