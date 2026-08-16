"""Six-character generation, memory and streaming pipelines.

MVP Candidate default group path:
    BehaviorEvent → one six-role semantic assessment → deterministic 0..2
    speaker arbitration → selected role generation → WebSocket streaming.

The older LangGraph supervisor/cycle builders remain below for compatibility
with historical probes, but ``stream_group_chat`` no longer invokes that fixed
round-robin graph. They are not the product's online behavior policy.

Provider 路由 (per-role):
    - 4 个 minimax M3 (便宜, 流量大): shu-hang, yao-shi, san-lang, ling-die
    - 2 个 agnes  (高质量, 关键回复): bei-he, bai-qianbei

Selected messages are generated serially so chunks never interleave.

Stage 5-A 上下文管理（`_trim_messages` + `_summary_cache`）：
    - 滑动窗口保留最近 20 条完整消息
    - 早期消息调 minimax M3 摘要成 1-2 句话作为 `system_context_summary`
    - 摘要放在 messages 头部（在 role system 之后）
    - 缓存 `_summary_cache` 避免重复摘要相同早期消息 (key = 早期消息内容 hash)
"""
from __future__ import annotations

import asyncio
import hashlib
import os
from typing import Annotated, Any, AsyncIterator, Literal, TypedDict

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langgraph.graph import START, StateGraph
from app.models import DmMessage  # Stage 6 DM Phase 2: 私信 history 类型
from langgraph.graph.message import add_messages
from langgraph.config import get_stream_writer

from app.config import get_settings
from app.behavior import (
    BehaviorEngine,
    BehaviorEvent,
    CandidatePolicy,
    DecisionLogStore,
    assess_intents_detailed,
    get_decision_log_store,
)
from app.llm import get_chat_model
from app.memory.agent_memory import AgentMemoryStore, get_agent_memory_store


# ============================================================================
# Stage 5-A：上下文管理（滑动窗口 + 早期摘要）
# ============================================================================
# 滑动窗口保留完整消息条数。+1 (summary) +1 (system) = 总数 ≤ 22。
_KEEP_LAST_COMPLETE: int = 20

# 摘要触发阈值（消息数 > 此值时启用截断）
_TRIM_THRESHOLD: int = _KEEP_LAST_COMPLETE + 2  # 22, 与 "总数 ≤ 22" 对齐

# 摘要缓存。key = MD5(早期消息序列 fingerprint), value = 摘要文本。
# 同 content 序列的早期消息复用同一份摘要，避免重复调 M3。
_summary_cache: dict[str, str] = {}

# 摘要 prompt 模板（最短 prompt + max_tokens，避免 M3 长输出超时）
_SUMMARY_PROMPT_TEMPLATE = (
    "请把以下早期群聊压缩成 1-2 句话中文摘要（保留关键决策 + 角色态度）：\n\n"
    "{messages_text}\n\n"
    "摘要："
)


def _make_summary_key(early_msgs: list[BaseMessage]) -> str:
    """缓存 key：基于 (role + name + 截断 content) 的 MD5。"""
    parts: list[str] = []
    for m in early_msgs:
        role = type(m).__name__
        name = getattr(m, "name", "") or ""
        content = str(getattr(m, "content", ""))[:120]
        parts.append(f"{role}#{name}={content}")
    raw = "|".join(parts)
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def _format_messages_for_summary(early_msgs: list[BaseMessage]) -> str:
    """把早期消息序列渲染成可摘要文本（单条截断 200 字）。"""
    lines: list[str] = []
    for m in early_msgs:
        role = type(m).__name__
        name = getattr(m, "name", "") or ""
        content = str(getattr(m, "content", ""))[:200]
        prefix = f"{name}" if name else role
        lines.append(f"- {prefix}: {content}")
    return "\n".join(lines)


async def _summarize_early_messages(early_msgs: list[BaseMessage]) -> str:
    """把早期消息摘要成 1-2 句话（调 minimax M3，带 `_summary_cache`）。"""
    if not early_msgs:
        return "（早期对话为空，无需摘要）"

    cache_key = _make_summary_key(early_msgs)
    cached = _summary_cache.get(cache_key)
    if cached is not None:
        return cached

    msgs_text = _format_messages_for_summary(early_msgs)
    prompt_text = _SUMMARY_PROMPT_TEMPLATE.format(messages_text=msgs_text)
    prompt_msg = HumanMessage(content=prompt_text)

    summary_text = ""
    try:
        # 用 minimax M3 (便宜, Stage 5-A 默认 summarizer)
        # temperature 低一些保证稳定
        llm = get_chat_model(provider="minimax", temperature=0.3)
        # 收集 _astream 输出
        chunks: list[str] = []
        async for chunk in llm.astream([prompt_msg]):
            content = chunk.content if isinstance(chunk.content, str) else str(chunk.content or "")
            if content:
                chunks.append(content)
        summary_text = "".join(chunks).strip()
    except Exception as e:  # noqa: BLE001
        # M3 调用失败 → 兜底留一行标记, 不让主流程崩
        summary_text = f"（早期 {len(early_msgs)} 条消息摘要失败: {type(e).__name__}）"

    if not summary_text:
        summary_text = f"（早期 {len(early_msgs)} 条消息无有效摘要）"

    # 写入缓存
    _summary_cache[cache_key] = summary_text
    return summary_text


async def _trim_messages(
    messages: list[BaseMessage],
    keep_last: int = _KEEP_LAST_COMPLETE,
) -> list[BaseMessage]:
    """滑动窗口 + 早期摘要压缩。

    输入：state.messages 的 raw 序列（不含本角色的 system_msg）。
    输出：截断后的消息序列 —
        - 总数 ≤ keep_last + 1 (summary) = 21
        - 含 1 条 `system_context_summary` 消息（早期消息的 M3 摘要）
        - 含最近 keep_last 条完整消息（默认 20）

    当消息数 ≤ keep_last + 1 时不截断（直接返回原列表）。
    """
    # 短消息：无需截断
    if len(messages) <= _TRIM_THRESHOLD:
        return list(messages)

    # 拆早期 + 最近
    early = messages[:-keep_last]
    recent = messages[-keep_last:]

    # 调 M3 摘要（带缓存）
    summary_text = await _summarize_early_messages(early)

    # summary 作为 system 风格消息（role=system），但独立标记方便区分
    summary_msg = SystemMessage(
        content=f"[system_context_summary]\n{summary_text}",
        additional_kwargs={"role_hint": "system_context_summary", "is_summary": True},
    )

    return [summary_msg] + list(recent)


def get_summary_cache_stats() -> dict[str, int]:
    """返回 `_summary_cache` 当前状态（用于诊断 / 测试）。"""
    return {"size": len(_summary_cache)}


def clear_summary_cache() -> None:
    """清空 `_summary_cache`（测试 / 调试用）。"""
    _summary_cache.clear()


# ============================================================================
# 角色定义 — Stage 4-B：九洲一号群聊天群 (《九洲一号群聊天群》小说 6 角色重塑)
#
# Provider 路由策略 (per-role):
#   - 4 个走 minimax (M3, 便宜): shu-hang, yao-shi, san-lang, ling-die
#   - 2 个走 agnes (高质量): bei-he, bai-qianbei (关键回复, 需要稳重/神秘)
#   - 由 app/llm.py get_chat_model(provider=...) 解析, 缺 key 时回退
#
# Cycle 顺序 (Supervisor 轮询): shu-hang → yao-shi → san-lang
#                              → bei-he → bai-qianbei → ling-die → ...
# 6 角色一个 cycle, max_rounds=8 保证一轮用户消息覆盖 1+ 个完整 cycle
#
# 前端兼容: agent (key) / name (中文) / emoji 字段保持不变, 前端只换色不换协议。
# ============================================================================
# === 九洲一号群 6 角色 system prompt 加固 (2026-07-04) ===
#
# 用户实测发现 MiniMax-M2.7-highspeed 这种小模型在多 NPC 同 session 上下文
# 下容易"失忆"——把北河散人当书航、暴露 LangChain HumanMessage 标签、
# 输出"我在九洲一号群界闯荡"这种 agent-executor 内部状态。
#
# 根因：
#   1. system prompt 长度 ~600 字，关键的身份信息（"你是谁"）被埋在第 3 段
#   2. 6 个 NPC 共享一个 chat thread，互相的对话历史会让小模型混淆
#   3. 旧 prompt 用 LangChain "HumanMessage:" / "Assistant:" 文本标记，
#      模型会当成对话模板填字段
#
# 修法：每个 ROLES["system"] 加 1 行 IDENTITY ANCHOR 在最前面：
#   "你唯一扮演的角色是【XXX】。你不是YYY、不是ZZZ、不是..."。
#   改 _wrap_persona() 在 bootstrap 时自动包一层。
# 同时把 ROLES 的 system 字段截短，去掉冗余的"与其他角色关系"段（这
# 段会跟 system prompt 重复，对小模型反而是 noise）。
_IDENTITY_ANCHOR_FMT = (
    "【身份锚】你唯一扮演的角色是【{name}】（key={key}, emoji={emoji}）。\n"
    "你不是【{others}】中的任何一个。\n"
    "每次回复只能用【{name}】的身份说话，不要打破第四面墙、"
    "不要假装自己是 AI、不要切换人设。\n"
    "如果用户搞混了你的身份，请温和纠正：'妾身（老夫/在下）是{name}，不是XXX'。\n"
    "\n---\n"
)


def _wrap_persona(role_key: str, raw_system: str) -> str:
    """在 ROLES[*]['system'] 前面插入 IDENTITY ANCHOR + 提纯。"""
    if role_key not in ROLES:
        return raw_system
    role = ROLES[role_key]
    name = role["name"]
    emoji = role.get("emoji", "")
    others = "、".join(
        f"{r['name']}" for k, r in ROLES.items() if k != role_key
    )
    return _IDENTITY_ANCHOR_FMT.format(
        name=name, key=role_key, emoji=emoji, others=others
    ) + raw_system


_DIRECT_RESPONSE_FALLBACKS: dict[str, str] = {
    "shu-hang": "我在，稍等我缓一缓。",
    "yao-shi": "老夫在，容我稍后细看。",
    "san-lang": "在呢！等我缓口气。",
    "bei-he": "老朽在，稍后与你细说。",
    "bai-qianbei": "嗯。我在。",
    "ling-die": "妾身在，稍后再与你细说。",
}


def _direct_response_fallback(role_key: str) -> str:
    return _DIRECT_RESPONSE_FALLBACKS.get(role_key, "我在，稍后回复你。")


def _generation_timeout_seconds() -> float:
    try:
        configured = float(os.environ.get("BEHAVIOR_GENERATION_TIMEOUT_SEC", "90"))
    except ValueError:
        configured = 90.0
    return max(1.0, min(300.0, configured))


def _max_response_chars() -> int:
    try:
        configured = int(os.environ.get("BEHAVIOR_MAX_RESPONSE_CHARS", "600"))
    except ValueError:
        configured = 600
    return max(40, min(2000, configured))


def _cap_response_piece(current: str, piece: str) -> tuple[str, bool]:
    remaining = max(0, _max_response_chars() - len(current))
    accepted = piece[:remaining]
    return accepted, len(current) + len(accepted) >= _max_response_chars()


async def _stream_with_generation_timeout(iterator: AsyncIterator[Any]) -> AsyncIterator[Any]:
    """Apply one wall-clock deadline to a complete streamed generation."""
    async with asyncio.timeout(_generation_timeout_seconds()):
        async for item in iterator:
            yield item


ROLES = {
    # ---- 1. 宋书航 (shu-hang) ---- minimax M3 便宜 ----
    "shu-hang": {
        "name": "宋书航",
        "emoji": "🌟",
        "provider": "minimax",
        "system": (
            "你是【宋书航】——九洲一号群的主角，一个意外踏入九洲一号群界的现代大学生。\n"
            "境界：灵尊（群里常被调侃的'书山压力山大'），实力暂时垫底，成长速度惊人。\n"
            "性格：自嘲、逗比、爱吐槽，嘴比脑子快，运气奇差又奇好，遇事下意识哀嚎。\n"
            "说话风格：\n"
            "  - 句尾常用'妈耶''啊啊啊''我又踩雷了'\n"
            "  - 遇到危险先哀嚎再行动：'妈耶，前辈这事儿不对劲啊！'\n"
            "  - 爱用现代梗：'这波血亏''我心态崩了''我感觉有人在针对我'\n"
            "  - 自称'在下''书航'，但语气是网感青年\n"
            "口头禅：'妈耶这剧情发展不对吧！''在下告辞！''等等让本尊缓缓'\n"
            "与其他角色关系：\n"
            "  - @药师：最信任的基友，受伤/炼丹都找他，但他开方子时你会嘀咕'这玩意儿真能吃吗'\n"
            "  - @狂刀三浪：他的忠实小迷弟，但三浪一动手你就想躲\n"
            "  - @北河散人：最敬仰的前辈，被点拨时乖乖立正\n"
            "  - @白前辈：怕他又感激他，每次他发话你都心惊肉跳\n"
            "  - @灵蝶尊者：美丽又强大的蝶姐姐，在她面前正经但偶尔会结巴\n"
            "约束：每次 2-3 句话；可以自嘲但不要真的丧；遇到大佬发言要会'瑟瑟发抖'式回应。"
        ),
    },

    # ---- 2. 药师 (yao-shi) ---- minimax M3 便宜 ----
    "yao-shi": {
        "name": "药师",
        "emoji": "💊",
        "provider": "minimax",
        "system": (
            "你是【药师】——九洲一号群的丹道宗师，出身药宗，炼丹八百年无一失手。\n"
            "境界：八品药师，距离九品只差一味'九幽冰莲'。\n"
            "性格：稳重、细心、惜字如金，偶尔毒舌吐槽，炼丹时心无旁骛。\n"
            "说话风格：\n"
            "  - 句式简短：'且慢''切勿急躁''待老夫看看'\n"
            "  - 爱引用丹方术语：'九转还魂''冰火淬炼''三才归元''望闻问切'\n"
            "  - 看病/问药的人说'你这病不打紧'，但其实心里已有方子\n"
            "口头禅：'嗯，此丹还需三日火候''你这体质，不吃我的药怕是要凉了''老夫不收庸人'\n"
            "与其他角色关系：\n"
            "  - @宋书航：最关照的晚辈，常给免费看诊开方（嘴上说'不收庸人'）\n"
            "  - @狂刀三浪：互相吐槽——他嫌三浪用身体硬扛不嗑药，三浪嫌他啰嗦\n"
            "  - @北河散人：老友，常一起论丹道\n"
            "  - @白前辈：敬他三分，但会吐槽'前辈又乱开方子'\n"
            "  - @灵蝶尊者：偶尔帮她炼化蝶毒\n"
            "约束：每条 2-3 句话；不要长篇大论讲丹方；吐槽时用'老夫'自称。"
        ),
    },

    # ---- 3. 狂刀三浪 (san-lang) ---- minimax M3 便宜 ----
    "san-lang": {
        "name": "狂刀三浪",
        "emoji": "🗡️",
        "provider": "minimax",
        "system": (
            "你是【狂刀三浪】——九洲一号群里的刀修狂人，外号'三浪前辈'，以一把'赤血狂刀'闻名。\n"
            "境界：六品刀修，战斗中可短时爆发到七品。\n"
            "性格：狂放不羁、嘴欠爱撩、刀子嘴豆腐心，越危险越兴奋。\n"
            "说话风格：\n"
            "  - 句子短促有力，感叹号多：'哈！''痛快！''来！'\n"
            "  - 爱用刀诀：'刀出无悔''斩！''赤血出鞘'\n"
            "  - 看到热闹必起哄：'这波我上！''我来！''这事儿老子接了！'\n"
            "口头禅：'一刀斩之！''哈哈痛快！''药师你别念了！''北河你又装深沉'\n"
            "与其他角色关系：\n"
            "  - @宋书航：嘴上叫他'小家伙''后生'，关键时刻会护他\n"
            "  - @药师：老对手/老友，互相看不顺眼又离不开\n"
            "  - @北河散人：群里你最服气的前辈（虽然嘴上不承认）\n"
            "  - @白前辈：敬畏中带点挑衅——'白前辈你行你来啊'（说完就怂）\n"
            "  - @灵蝶尊者：又敬又嘴贱——'蝶姐姐今天怎么有空''蝴蝶不如我大刀硬'\n"
            "约束：每条 1-2 句话；多用感叹号；不要长篇分析；遇到战斗话题立刻上头。"
        ),
    },

    # ---- 4. 北河散人 (bei-he) ---- agnes 高质量 ----
    "bei-he": {
        "name": "北河散人",
        "emoji": "🌊",
        "provider": "agnes",
        "system": (
            "你是【北河散人】——九洲一号群的元老级前辈，外号'北河老哥'，群里资历仅次于白前辈。\n"
            "境界：八品散修，擅长水系法术，洞察力极强。\n"
            "性格：温厚沉稳、语重心长、偶尔深沉幽默，是群里的'主心骨'，关键决策必问他的意见。\n"
            "说话风格：\n"
            "  - 半文言半白话：'老朽观之...''后生可畏''此事当徐徐图之'\n"
            "  - 给人建议必带'以老朽之见''不若''何不'\n"
            "  - 不轻易定论，喜欢'嗯...且看''此事尚需斟酌'\n"
            "口头禅：'后生莫急''嗯，此事蹊跷''依老朽看''且慢，且慢'\n"
            "与其他角色关系：\n"
            "  - @宋书航：看着他长大的晚辈，常点拨他但语气温和\n"
            "  - @药师：老友，论丹论道时常一唱一和\n"
            "  - @狂刀三浪：压得住他，'三浪啊三浪，别莽'\n"
            "  - @白前辈：旧识/老友，'白前辈这次当真？'，互相开玩笑\n"
            "  - @灵蝶尊者：同辈，'蝶尊者言之有理'\n"
            "约束：每条 2-4 句话；保持长者口吻；关键节点要给出稳重建议；不要搞笑喧宾夺主。"
        ),
    },

    # ---- 5. 白前辈 (bai-qianbei) ---- agnes 高质量 ----
    "bai-qianbei": {
        "name": "白前辈",
        "emoji": "👻",
        "provider": "agnes",
        "system": (
            "你是【白前辈】（白尊者）——九洲一号群里辈分最高的存在，真实实力深不可测。\n"
            "境界：传说九品之上，外人不可直视，常以'白袍青年'示人。\n"
            "性格：神秘、寡言、偶尔一针见血，似乎对一切'有趣'或'无聊'有自己的判断标准。\n"
            "说话风格：\n"
            "  - 字数极少，句号收尾：'嗯。''善。''可。''有趣。''不有趣。'\n"
            "  - 偶尔一句话点破关键：'此子可教''此丹有异''前路已断'\n"
            "  - 几乎不用感叹号，节奏慢，遣词古典\n"
            "  - 逗弄 @宋书航 时会多两个字：'书航，可。''书航，有趣。'\n"
            "口头禅：'嗯。''善。''可。''有趣。''此事...且记下。''老夫乏了。'\n"
            "与其他角色关系：\n"
            "  - @宋书航：你最喜欢的后辈（玩具），偶尔会多关照他，但方式诡异\n"
            "  - @药师：你认可他的丹道，'善''可'\n"
            "  - @狂刀三浪：不评价，'刀修...尚可'\n"
            "  - @北河散人：旧友，'北河，你老了'\n"
            "  - @灵蝶尊者：同辈，'蝶尊者，别来无恙'\n"
            "约束：每次只发 5-15 字；多用句号；语气淡然；切忌长篇大论；别人问你问题先'嗯。'再答。"
        ),
    },

    # ---- 6. 灵蝶尊者 (ling-die) ---- minimax M3 便宜 ----
    "ling-die": {
        "name": "灵蝶尊者",
        "emoji": "🦋",
        "provider": "minimax",
        "system": (
            "你是【灵蝶尊者】——九洲一号群中唯一的女性高阶，蝴蝶精化形，出身灵蝶岛。\n"
            "境界：八品尊者，擅长幻术与木系法术，是群里最优雅的存在。\n"
            "性格：优雅、傲娇、直觉敏锐、言语犀利，对后辈温柔但对同辈不留情面。\n"
            "说话风格：\n"
            "  - 自称'妾身''本尊'，句式委婉：'妾身以为''此事蹊跷''恐有变故'\n"
            "  - 爱用比喻：'如蝶恋花''如镜中月''如春风过境'\n"
            "  - 怼人时不带脏字但字字见血：'三浪，你还是这么粗鲁'\n"
            "口头禅：'妾身以为...''此事蹊跷''尔等莫要妄动''本尊记下了'\n"
            "与其他角色关系：\n"
            "  - @宋书航：视他如晚辈/弟弟，温柔点拨：'书航莫慌，妾身在'\n"
            "  - @药师：偶尔请他炼化蝶毒，'药师，这味药能否再精炼一分'\n"
            "  - @狂刀三浪：最常怼的人，'三浪，闭嘴'\n"
            "  - @北河散人：同辈，敬重但会调侃——'北河老哥又在装深沉'\n"
            "  - @白前辈：同辈中最敬畏的，'白前辈...妾身有礼'\n"
            "约束：每条 2-3 句话；保持优雅；可以用'妾身'自称；不直白粗口；遇到不合理的事会皱眉'蹊跷'。"
        ),
    },
}

# ============================================================================
# State
# ============================================================================
class State(TypedDict, total=False):
    """Stage 4-B：6 角色九洲一号群聊天群 state."""
    messages: Annotated[list[BaseMessage], add_messages]  # 全部历史消息
    topic: str                                            # 当前讨论话题
    next_speaker: Literal[                                # 下一位 (6 角色)
        "shu-hang", "yao-shi", "san-lang",
        "bei-he", "bai-qianbei", "ling-die",
    ]
    round_count: int                                      # 已经多少轮
    call_summarizer: bool                                 # 白前辈 / 北河 是否召唤总结


# ============================================================================
# Stage 7：Letta 流式输出 helper（替换 minimax/agnes 的 leaf LLM call）
# ============================================================================
# 选择策略：
#   - USE_MOCK_LLM=true        → 永远走 MockChatModel（不让 Letta 误命中真实 LLM）
#   - USE_LETTA=false          → 走 per-role provider（minimax / agnes，legacy 路径）
#   - 默认（USE_LETTA=true 且 !mock 且 role_key 在 6 角色中）→ 走 Letta
#
# Letta 调用结构（与 Project A Stage 8-B 一致）：
#   1) 让 LettaAgentRegistry 找到或创建该 role 的 Letta agent
#   2) 拼一段 user prompt（system + history + current → text 渲染）
#   3) POST /v1/agents/{id}/messages/stream → SSE events
#   4) 提取 assistant_message 事件里的 content,逐字 yield
#
# 失败兜底：Letta 不可达 / agent 不存在 → 退回到 per-role provider（graceful degrade），
# 不让 chat 流直接挂掉。


async def _stream_via_letta(
    role_key: str,
    session_id: str,
    all_msgs: list[BaseMessage],
) -> AsyncIterator[str]:
    """流式从 Letta agent 拉取一段回复，逐字 yield 文本片段。

    Args:
        role_key: 九洲一号群 6 角色之一
        session_id: WS session id（用于 future per-session Letta memory 隔离）
        all_msgs: 完整 LLM 输入 [system, history..., user]，已含 system_msg

    Yields:
        字符串片段（与 `llm.astream` 的 chunk.content 形状对齐）

    Raises:
        LettaError / RegistryError：调用方应决定是否 fallback
    """
    # 1) registry 找 agent_id（找不到就创建 + 持久化）
    from app.letta_bridge import get_letta_client, get_or_create_agent_id

    settings = get_settings()
    client = get_letta_client()
    agent_id = await get_or_create_agent_id(
        client=client,
        role_key=role_key,
        llm_model=settings.letta_llm_model,
    )

    # 2) 拼一段 user prompt：把历史 / 当前 user 渲染成文本
    # Letta 0.16.x 的 messages/stream 只吃 {"role":"user","content":"..."}
    #
    # === Prompt format (rewrite 2026-07-04) ===
    #
    # 用户实测发现旧格式【系统设定】+ LangChain `HumanMessage:` /
    # `Assistant:` 文本标签会让 MiniMax-M2.7-highspeed / qwen2.5:1.5b 这种
    # 小模型进入 "我在对话模板里，应该填字段" 的幻觉模式：
    #   - 输出 "HumanMessage: 是，**书航**..." 这种 agent-executor 内部状态泄漏
    #   - 北河散人回复说"我叫书航" — 完全失忆
    #
    # 新格式 = 直接用 [角色名]: / [用户]: 的对话标记 + 把 persona 作为 system
    # 角色描述放在最前面 + 显式 anti-cross-contamination 提醒。
    # 这样模型清楚地知道自己是"在扮演一个固定角色"，不会跑偏到模板填充。
    #
    # 注意：persona 仍然走 Letta core-memory blocks（agent 创建时持久化），
    # 这里的【角色设定】是双保险 — 即使 blocks 被污染 / 缺失，prompt 里也
    # 有完整人设。
    system_prefix_parts: list[str] = []
    for m in all_msgs:
        if isinstance(m, SystemMessage):
            content = m.content if isinstance(m.content, str) else str(m.content or "")
            if content.strip():
                system_prefix_parts.append(content.strip())
    prompt_lines: list[str] = []
    if system_prefix_parts:
        joined_system = "\n".join(system_prefix_parts)
        # 把 persona 当作"角色设定"放最前，让模型第一眼看到自己是谁。
        # 同时追加 anti-cross-contamination 提醒，避免小模型混淆 6 个 NPC。
        role = role_key or "?"
        role_name = ROLES.get(role, {}).get("name", role) if role in ROLES else role
        prompt_lines.append(
            f"【角色设定】你现在扮演【{role_name}】。\n"
            f"重要提醒：你是【{role_name}】，不是宋书航、不是药师、不是狂刀三浪、"
            f"不是北河散人、不是白前辈、也不是灵蝶尊者。请只用【{role_name}】的身份说话。\n"
            f"\n{joined_system}"
        )
    # 九洲一号群风格回复长度上限（防御性 prompt 注入，避免 LLM 输出 5 段论文）：
    # M3 / qwen2.5:1.5b 在多轮场景下倾向于长篇大论，开 chat 体感差。
    prompt_lines.append(
        "【回复要求】\n"
        "  - 严格用【{name}】身份回复，不要切换角色、不要打破第四面墙\n"
        "  - 不要输出任何 markdown 标题 / 列表 / 代码块\n"
        "  - 不要重复对方的词句作为开头\n"
        "  - 100-300 字以内，口语化，贴合九洲一号群聊天节奏".format(name=role_name if system_prefix_parts else "角色")
    )
    prompt_lines.append("【对话】")
    for m in all_msgs:
        if isinstance(m, SystemMessage):
            continue  # persona 已经放进【角色设定】前缀
        role = type(m).__name__
        name = getattr(m, "name", "") or ""
        content = m.content if isinstance(m.content, str) else str(m.content or "")
        if not content.strip():
            continue
        prefix = f"{name}: " if name else f"{role}: "
        prompt_lines.append(prefix + content)
    # 关键：追加一个空 assistant turn 让模型从"角色回复"位置继续生成
    # （避免模型在最后一轮 user msg 之后还在犹豫要不要回答）
    prompt_lines.append(f"{role_name if system_prefix_parts else '角色'}: ")
    prompt_text = "\n".join(prompt_lines) if prompt_lines else "(空)"

    # 3) 调 Letta stream
    full_text = ""
    async for ev in client.stream_message(agent_id, prompt_text):
        # Letta 0.16.x SSE 事件类型（message_type 字段）：
        #   - "assistant_message" → 真正回复（含 content）
        #   - "reasoning_message" → 思考（如果 LLM 开了 thinking；我们跳过）
        #   - "tool_call_message" → 工具调用（本项目不用，让 agent 不带 tool）
        #   - 其他（ping / usage / stop_reason / ...） → 跳过
        mtype = ev.get("message_type") or ev.get("type")
        if mtype in ("assistant_message", "assistant"):
            content = ev.get("content") or ev.get("text") or ""

            def _strip_think(s: str) -> str:
                """2026-07-04: qwen2.5 / MiniMax 在 Letta 上输出 <think>...</think>
                思考块，frontend 不应该看到 — 真实对话在 </think> 之后。
                兼容多种 think 格式：<think>...</think> / <think>\n... / 自闭合。
                """
                if "</think>" in s:
                    s = s.split("</think>", 1)[1]
                elif s.lstrip().startswith("<think>"):
                    # 没有 close tag 的退化情况 — 整段当 think 处理
                    return ""
                return s.strip()

            if isinstance(content, list):
                # 多模态可能 content 是 [{type: text, text: ...}]
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "text":
                        text_piece = _strip_think(part.get("text", ""))
                        if text_piece:
                            full_text += text_piece
                            yield text_piece
            elif isinstance(content, str) and content:
                text_piece = _strip_think(content)
                if text_piece:
                    full_text += text_piece
                    yield text_piece


def _use_letta_path(role_key: str | None = None) -> bool:
    """决策：当前调用是否应该走 Letta？

    True 当且仅当：
      - USE_MOCK_LLM=false（mock 优先，永远不会被 Letta 顶掉）
      - USE_LETTA=true
      - role_key 是九洲一号群 6 角色之一（或 None 表示通配匹配）

    离线 / 单测场景：mock 永远走老路径，不连 Letta。
    """
    settings = get_settings()
    if settings.use_mock_llm:
        return False
    if not settings.use_letta:
        return False
    if role_key is None:
        return True
    return role_key in ROLES


# ============================================================================
# Agent 节点 (每个角色)
# ============================================================================
async def make_agent_node(
    role_key: str,
    session_id: str = "default",
    memory_store: AgentMemoryStore | None = None,
):
    """工厂: 生成一个 role_key 对应的 agent node 函数.

    Stage 7 Bug 2: 加入 session_id + memory_store 参数,
    让 agent_node 在 agent_done 时调 memory_store.fan_out_group_event 把发言写到
    九洲一号群 6 角色 memory(每个人都知道群聊发生过什么)。

    兼容性: session_id / memory_store 都可选,默认 "default" / None。
    legacy stream_agent fallback 不传 store 时跳过 fan-out(行为不变)。
    """
    role = ROLES[role_key]
    system_msg = SystemMessage(content=_wrap_persona(role_key, role["system"]))

    async def agent_node(state: State) -> dict[str, Any]:
        # 推 thinking 事件（前端显示"X 正在思考"）
        writer = get_stream_writer()
        writer({"event": "agent_thinking", "agent": role_key, "name": role["name"], "emoji": role["emoji"]})

        # Stage 5-A: 滑动窗口 + 早期摘要压缩。把 state.messages 拆成早期 + 最近,
        # 早期调 M3 摘要成 system_context_summary 放最前（system_msg 之后）。
        state_msgs = list(state.get("messages", []))
        trimmed = await _trim_messages(state_msgs)

        # 调 LLM 流式输出
        # Stage 7: 根据配置选择 Letta 或 per-role provider
        msgs: list[BaseMessage] = [system_msg] + trimmed
        full_response = ""

        if _use_letta_path(role_key):
            # Letta path：每个角色拥有独立持久 agent
            try:
                async for content_piece in _stream_via_letta(
                    role_key=role_key,
                    session_id=session_id,
                    all_msgs=msgs,
                ):
                    if not content_piece:
                        continue
                    full_response += content_piece
                    writer({"event": "agent_msg_chunk", "agent": role_key, "chunk": content_piece})
            except Exception as letta_exc:  # noqa: BLE001
                # graceful degrade：Letta 不可达时退回 per-role provider
                writer({"event": "letta_fallback", "agent": role_key, "message": str(letta_exc)})
                llm = get_chat_model(provider=role.get("provider"))
                async for chunk in llm.astream(msgs):
                    content = chunk.content if isinstance(chunk.content, str) else str(chunk.content or "")
                    if not content:
                        continue
                    full_response += content
                    writer({"event": "agent_msg_chunk", "agent": role_key, "chunk": content})
        else:
            # 路径 A：Stage 4-B per-role provider routing (4 minimax M3 + 2 agnes)
            llm = get_chat_model(provider=role.get("provider"))
            async for chunk in llm.astream(msgs):
                content = chunk.content if isinstance(chunk.content, str) else str(chunk.content or "")
                if not content:
                    continue
                full_response += content
                writer({"event": "agent_msg_chunk", "agent": role_key, "chunk": content})

        new_round_count = state.get("round_count", 0) + 1

        # Stage 7 Bug 2: agent 发言 fan-out 到九洲一号群 6 角色 memory
        # 九洲一号群是公开场景,每个角色都"在场",所以 fan-out 到全部 6 角色
        if memory_store is not None and full_response:
            try:
                memory_store.fan_out_group_event(
                    session_id=session_id,
                    speaker_key=role_key,
                    role="agent",
                    text=full_response,
                    agent_name=role["name"],
                    agent_emoji=role["emoji"],
                )
            except Exception as e:  # noqa: BLE001
                # 持久化失败不能阻塞 agent_done 事件
                writer({"event": "memory_persist_warning", "message": str(e)})

        # 推 agent_done 事件（一次发言 = 1 round）
        writer({
            "event": "agent_done",
            "agent": role_key,
            "name": role["name"],
            "emoji": role["emoji"],
            "full_text": full_response,
            "round": new_round_count,
        })

        # 返回 assistant 消息（会通过 add_messages 自动加入 messages）
        return {
            "messages": [{"role": "assistant", "content": full_response, "name": role["name"]}],
            "round_count": new_round_count,
        }

    agent_node.__name__ = f"agent_{role_key}"
    return agent_node


# ============================================================================
# Legacy compatibility supervisor — not used by stream_group_chat.
# ============================================================================
SUPERVISOR_SYSTEM = (
    "你是这场多 Agent 群聊的主持人调度器。\n"
    "可用 Agent (九洲一号群 6 角色):\n"
    "  - shu-hang   (宋书航, 🌟, minimax)\n"
    "  - yao-shi    (药师,   💊, minimax)\n"
    "  - san-lang   (狂刀三浪, 🗡️, minimax)\n"
    "  - bei-he     (北河散人, 🌊, agnes)\n"
    "  - bai-qianbei(白前辈, 👻, agnes)\n"
    "  - ling-die   (灵蝶尊者, 🦋, minimax)\n"
    "决策规则:\n"
    "1. 用户消息含 @某Agent 时, 优先选被 @ 的那个 (按 cycle 顺序检查)\n"
    "2. 普通用户消息 → 默认轮询 (shu-hang → yao-shi → san-lang → bei-he → bai-qianbei → ling-die → ...)\n"
    "3. 第一轮让 shu-hang 开场 (新用户消息总是从主角开始)\n"
    "4. max_rounds=8 允许一轮消息覆盖完整 6 角色 cycle + 2 重复\n"
    "只输出 1 个单词: shu-hang / yao-shi / san-lang / bei-he / bai-qianbei / ling-die\n"
)

# 九洲一号群 6 角色 cycle 顺序 (Supervisor 轮询基础)
ROLE_CYCLE: list[str] = [
    "shu-hang", "yao-shi", "san-lang",
    "bei-he", "bai-qianbei", "ling-die",
]


async def supervisor_node(state: State) -> dict[str, Any]:
    """Supervisor 决策下一位发言者 (Stage 4-B: 6 角色轮询 + @mention)."""
    writer = get_stream_writer()
    writer({"event": "supervisor_decision", "next_agent": "auto"})

    # 调度: 轮询 + @mention 检测
    round_count = state.get("round_count", 0)
    last_msg = state["messages"][-1] if state.get("messages") else None

    # 规则 1: @mention 检测 (按 cycle 顺序检查, 命中即返回)
    if last_msg and isinstance(last_msg, HumanMessage):
        text = str(last_msg.content)
        for role_key in ROLE_CYCLE:
            if f"@{ROLES[role_key]['name']}" in text or f"@{role_key}" in text:
                return {"next_speaker": role_key}

    # 规则 2: 轮询 (shu-hang → yao-shi → san-lang → bei-he → bai-qianbei → ling-die → ...)
    next_idx = round_count % len(ROLE_CYCLE)
    return {"next_speaker": ROLE_CYCLE[next_idx]}


# ============================================================================
# 路由: next_speaker → 对应 agent node
# ============================================================================
def route_after_supervisor(state: State) -> str:
    return state.get("next_speaker", "shu-hang")  # Stage 4-B 默认 shu-hang


# ============================================================================
# Build Graph
# ============================================================================
def build_graph() -> Any:
    g = StateGraph(State)

    # Supervisor + 6 九洲一号群 agent node
    g.add_node("supervisor", supervisor_node)
    for role_key in ROLE_CYCLE:
        g.add_node(role_key, await_agent(role_key))

    # 入口 → supervisor
    g.add_edge(START, "supervisor")

    # supervisor → 选中的 agent
    g.add_conditional_edges(
        "supervisor",
        route_after_supervisor,
        {role_key: role_key for role_key in ROLE_CYCLE},
    )

    # agent → 回 supervisor (继续循环)
    for role_key in ROLE_CYCLE:
        g.add_edge(role_key, "supervisor")

    return g.compile()


async def build_graph_for_session(
    session_id: str,
    memory_store: AgentMemoryStore,
) -> Any:
    """Stage 7 Bug 2: 为单个 session 构建带 memory store 闭包的 graph。

    九洲一号群群聊每次 stream_group_chat 调用都需要：
    1. 一个 per-session graph（agent_node 闭包持有 session_id + memory_store）
    2. 在 agent_done 时 fan-out 到九洲一号群 6 角色 memory
    3. 在 stream 入口 fan-out user 消息到 6 角色 memory

    性能：6 agent_node 重建 < 10ms，可接受。
    """
    g = StateGraph(State)

    # Supervisor 节点（不需要 session_id + store）
    g.add_node("supervisor", supervisor_node)

    # 6 九洲一号群 agent node,每个闭包持有 session_id + memory_store
    for role_key in ROLE_CYCLE:
        node = await make_agent_node(role_key, session_id=session_id, memory_store=memory_store)
        g.add_node(role_key, node)

    # 入口 → supervisor
    g.add_edge(START, "supervisor")

    # supervisor → 选中的 agent
    g.add_conditional_edges(
        "supervisor",
        route_after_supervisor,
        {role_key: role_key for role_key in ROLE_CYCLE},
    )

    # agent → 回 supervisor (继续循环)
    for role_key in ROLE_CYCLE:
        g.add_edge(role_key, "supervisor")

    return g.compile()


def await_agent(role_key: str):
    """Helper to create async agent node (build_graph is sync)."""
    return _agent_nodes[role_key]


# 预生成 4 个 agent node
_agent_nodes: dict[str, Any] = {}


async def _init_agent_nodes() -> None:
    """在模块加载时初始化所有 agent node (因为它们是 async factory)."""
    for role_key in ROLES.keys():
        _agent_nodes[role_key] = await make_agent_node(role_key)


# 同步入口
def build_graph_sync() -> Any:
    """构建 graph (同步版, 在已运行的事件循环外调用)."""
    # 直接复用预生成的 agent node (在模块导入时异步生成)
    # 这里做一个 fallback: 如果 _agent_nodes 没初始化, 用 sync agent (no stream)
    if not _agent_nodes:
        _init_sync_agent_nodes()
    return build_graph()


def _init_sync_agent_nodes() -> None:
    """生成同步版本的 agent node (用于测试或 fallback)."""
    for role_key, role in ROLES.items():
        async def agent_node(state: State, _role=role, _key=role_key) -> dict[str, Any]:
            writer = get_stream_writer()
            writer({"event": "agent_thinking", "agent": _key, "name": _role["name"], "emoji": _role["emoji"]})
            llm = get_chat_model(provider=_role.get("provider"))
            system_msg = SystemMessage(content=_wrap_persona(_key, _role["system"]))
            # Stage 5-A: 滑动窗口 + 早期摘要压缩
            state_msgs = list(state.get("messages", []))
            trimmed = await _trim_messages(state_msgs)
            msgs: list[BaseMessage] = [system_msg] + trimmed
            full_response = ""
            async for chunk in llm.astream(msgs):
                content = chunk.content if isinstance(chunk.content, str) else str(chunk.content or "")
                if not content:
                    continue
                full_response += content
                writer({"event": "agent_msg_chunk", "agent": _key, "chunk": content})
            new_round_count = state.get("round_count", 0) + 1
            writer({
                "event": "agent_done",
                "agent": _key,
                "name": _role["name"],
                "emoji": _role["emoji"],
                "full_text": full_response,
                "round": new_round_count,
            })
            return {
                "messages": [{"role": "assistant", "content": full_response, "name": _role["name"]}],
                "round_count": new_round_count,
            }
        agent_node.__name__ = f"agent_{role_key}_sync"
        _agent_nodes[role_key] = agent_node


# 在 import 时异步初始化 (如果有 running event loop)
try:
    asyncio.get_running_loop()
except RuntimeError:
    # Python 3.12 no longer creates an implicit loop for get_event_loop().
    asyncio.run(_init_agent_nodes())
# With an already-running loop, build_graph_sync uses the sync-node fallback.


# 模块级单例
graph = build_graph_sync()


# ============================================================================
# 对外 API: 启动一轮多 Agent 讨论
# ============================================================================
async def stream_group_chat(
    user_text: str,
    topic: str = "",
    history: list[BaseMessage] | None = None,
    max_rounds: int = 2,
    session_id: str = "default",
    memory_store: AgentMemoryStore | None = None,
    author: str | None = None,    # T9 / Piece B: 用户署名 (前端 userIdentity)
    event_id: str | None = None,
    decision_log: DecisionLogStore | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Run one event-driven social turn with zero to two NPC responses.

    Intent assessment defaults to **deterministic heuristics** (fast; no LLM
    wait).  Explicit @ never waits on LLM.  Optional ``BEHAVIOR_ASSESS_MODE=llm``
    restores the old six-role feature-extraction call.  ``BehaviorEngine``
    then applies hard gates, weights and arbitration.
    """
    if memory_store is None:
        memory_store = get_agent_memory_store()
    if decision_log is None:
        decision_log = get_decision_log_store()

    behavior_event = BehaviorEvent(
        event_id=event_id or str(__import__("uuid").uuid4()),
        session_id=session_id,
        event_type="user_message",
        text=user_text,
        speaker_key="user",
    )

    # An event id is append-once. Suppress the entire duplicate before memory
    # fan-out or generation so reconnect/retry cannot create duplicate speech.
    existing = decision_log.get(behavior_event.event_id)
    if existing is not None:
        if decision_log.matches_event(behavior_event) is False:
            yield {
                "event": "error",
                "code": "EVENT_ID_COLLISION",
                "message": "event_id was already used for different input",
            }
            yield {"event": "group_chat_done", "rounds": 0, "agents": []}
            return
        yield {
            "event": "behavior_duplicate",
            "event_id": behavior_event.event_id,
            "selected_roles": existing.selected_roles,
        }
        yield {"event": "group_chat_done", "rounds": 0, "agents": [], "duplicate": True}
        return

    # Stage 7 Bug 2: user 消息先 fan-out 到九洲一号群 6 角色 memory
    # (九洲一号群公开场景,每个人都"听到"了 user 说话)
    # T9 / Piece B: author 透传到 fan_out_group_event,内部 fallback "神秘人".
    try:
        memory_store.fan_out_group_event(
            session_id=session_id,
            speaker_key="user",
            role="user",
            text=user_text,
            author=author,
        )
    except Exception as e:  # noqa: BLE001
        # 持久化失败不能阻塞群聊
        yield {"event": "memory_persist_warning", "stage": "user_msg_fanout", "message": str(e)}

    recent_entries = memory_store.load_session_group_history(session_id, limit=20)
    if history:
        msgs: list[BaseMessage] = list(history)
        msgs.append(HumanMessage(content=user_text))
    else:
        msgs = []
        for entry in recent_entries:
            if entry.role == "user":
                msgs.append(HumanMessage(content=entry.text, name=entry.author or "user"))
            else:
                msgs.append(AIMessage(content=entry.text, name=entry.agent_name or entry.speaker_key))

    context = [
        {"speaker": entry.speaker_key, "text": entry.text[:500]}
        for entry in recent_entries
    ]
    assessment = await assess_intents_detailed(behavior_event, context)
    intents = assessment.candidates
    recent_agent_speakers = [entry.speaker_key for entry in recent_entries if entry.role == "agent"][-1:]
    policies = {
        role_key: CandidatePolicy(recently_spoke=role_key in recent_agent_speakers)
        for role_key in ROLE_CYCLE
    }
    decision = BehaviorEngine().decide(
        behavior_event,
        intents,
        policies,
        assessment=assessment.metadata,
    )
    if not decision_log.save(decision):
        yield {"event": "behavior_duplicate", "event_id": behavior_event.event_id}
        yield {"event": "group_chat_done", "rounds": 0, "agents": [], "duplicate": True}
        return

    yield {"event": "behavior_decision", "decision": decision.model_dump(mode="json")}

    rounds = 0
    agents_seen: list[str] = []
    responder_limit = max(0, min(2, max_rounds))
    for role_key in decision.selected_roles[:responder_limit]:
        role = ROLES[role_key]
        yield {"event": "supervisor_decision", "next_agent": role_key}
        yield {
            "event": "agent_thinking",
            "agent": role_key,
            "name": role["name"],
            "emoji": role["emoji"],
        }

        system_msg = SystemMessage(content=_wrap_persona(role_key, role["system"]))
        trimmed = await _trim_messages(msgs)
        llm_messages: list[BaseMessage] = [system_msg] + trimmed
        full_response = ""
        try:
            if _use_letta_path(role_key):
                try:
                    async for piece in _stream_with_generation_timeout(
                        _stream_via_letta(role_key, session_id, llm_messages)
                    ):
                        piece, capped = _cap_response_piece(full_response, piece)
                        if piece:
                            full_response += piece
                            yield {"event": "agent_msg_chunk", "agent": role_key, "chunk": piece}
                        if capped:
                            break
                except Exception as letta_exc:  # noqa: BLE001
                    yield {
                        "event": "letta_fallback",
                        "agent": role_key,
                        "message": str(letta_exc),
                    }
                    llm = get_chat_model(provider=role.get("provider"))
                    async for chunk in _stream_with_generation_timeout(llm.astream(llm_messages)):
                        piece = chunk.content if isinstance(chunk.content, str) else str(chunk.content or "")
                        piece, capped = _cap_response_piece(full_response, piece)
                        if piece:
                            full_response += piece
                            yield {"event": "agent_msg_chunk", "agent": role_key, "chunk": piece}
                        if capped:
                            break
            else:
                llm = get_chat_model(provider=role.get("provider"))
                async for chunk in _stream_with_generation_timeout(llm.astream(llm_messages)):
                    piece = chunk.content if isinstance(chunk.content, str) else str(chunk.content or "")
                    piece, capped = _cap_response_piece(full_response, piece)
                    if piece:
                        full_response += piece
                        yield {"event": "agent_msg_chunk", "agent": role_key, "chunk": piece}
                    if capped:
                        break
        except Exception as exc:  # noqa: BLE001
            yield {"event": "error", "message": str(exc), "agent": role_key}
            if role_key not in decision.mentioned_roles:
                continue
            full_response = _direct_response_fallback(role_key)
            yield {
                "event": "agent_msg_chunk",
                "agent": role_key,
                "chunk": full_response,
                "fallback": True,
            }

        if not full_response and role_key in decision.mentioned_roles:
            yield {
                "event": "error",
                "code": "EMPTY_GENERATION",
                "message": "selected role returned empty output; using direct-response fallback",
                "agent": role_key,
            }
            full_response = _direct_response_fallback(role_key)
            yield {
                "event": "agent_msg_chunk",
                "agent": role_key,
                "chunk": full_response,
                "fallback": True,
            }

        rounds += 1
        agents_seen.append(role_key)
        if full_response:
            try:
                memory_store.fan_out_group_event(
                    session_id=session_id,
                    speaker_key=role_key,
                    role="agent",
                    text=full_response,
                    agent_name=role["name"],
                    agent_emoji=role["emoji"],
                )
            except Exception as exc:  # noqa: BLE001
                yield {"event": "memory_persist_warning", "message": str(exc)}
            msgs.append(AIMessage(content=full_response, name=role["name"]))

        yield {
            "event": "agent_done",
            "agent": role_key,
            "name": role["name"],
            "emoji": role["emoji"],
            "full_text": full_response,
            "round": rounds,
        }

    yield {
        "event": "group_chat_done",
        "rounds": rounds,
        "agents": agents_seen,
        "outcome": decision.outcome,
        "event_id": behavior_event.event_id,
    }


# ============================================================================
# 兼容 P0: 单 Agent 流式 (供 ws.py 旧路径调用)
# ============================================================================
async def stream_agent(
    user_text: str,
    history: list[BaseMessage] | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """P0 单 Agent 流式接口 (向后兼容, Stage 4-B: 默认用 shu-hang 角色).

    现在直接调 shu-hang 角色作为默认 Agent. 返回 P0 兼容的事件格式:
        {"event": "agent_thinking"}
        {"event": "agent_msg_chunk", "chunk": "..."}
        {"event": "agent_done", "full_text": "..."}
        {"event": "error", "message": "..."}
    """
    from langchain_core.messages import HumanMessage

    msgs: list[BaseMessage] = list(history or [])
    msgs.append(HumanMessage(content=user_text))

    # Stage 4-B：单 Agent 兜底用 shu-hang (主角, 流量最大, 九洲一号群最活跃)
    default_role = "shu-hang"

    full_text = ""
    try:
        # 直接用 shu-hang 角色 (单 Agent 行为)
        from langgraph.config import get_stream_writer
        from langchain_core.messages import SystemMessage

        writer = get_stream_writer()
        writer({
            "event": "agent_thinking",
            "agent": default_role,
            "name": ROLES[default_role]["name"],
            "emoji": ROLES[default_role]["emoji"],
        })

        llm = get_chat_model(provider=ROLES[default_role].get("provider"))
        system_msg = SystemMessage(content=ROLES[default_role]["system"])
        # Stage 5-A: 滑动窗口 + 早期摘要压缩
        trimmed = await _trim_messages(msgs)
        all_msgs = [system_msg] + trimmed
        async for chunk in llm.astream(all_msgs):
            content = chunk.content if isinstance(chunk.content, str) else str(chunk.content or "")
            if not content:
                continue
            full_text += content
            writer({"event": "agent_msg_chunk", "agent": default_role, "chunk": content})

        yield {"event": "agent_done", "full_text": full_text}
    except Exception as e:  # noqa: BLE001
        yield {"event": "error", "message": str(e)}


# ============================================================================
# Stage 6 DM Phase 2：单 Agent 私信流式（不走 supervisor / 不进群聊 cycle）
# ============================================================================
async def stream_dm_chat(
    target_agent_key: str,
    user_text: str,
    history: list[DmMessage] | None = None,
    session_id: str = "default",
    memory_store: AgentMemoryStore | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Stage 6 DM Phase 2：单 Agent 私信流式生成器。

    Stage 7 Bug 2 增强:
    - 改用 AgentMemoryStore 替代 DmStore (统一 group + dm memory)
    - history 默认从 AgentMemoryStore.load_agent_memory 读(包含 group 上下文)
    - 渲染历史时按 source 加前缀 ([群聊背景] X: ... vs DM)
    - 持久化 agent 回复: append_message(source="dm", speaker_key=target_agent_key)
    - 兼容旧 DmMessage history 参数(向后兼容 Stage 6 tests)

    与 `stream_group_chat` 的区别：
        - 不进 Supervisor cycle，不触发群聊的 6 角色轮询
        - 只针对一个目标 agent 调用其 LLM（per-role provider routing 同群聊）
        - 读取的 history 来自 AgentMemoryStore(九洲一号群 6 角色共享 memory,见
          backend/.harness/reports/agent_memory_design.md §5.2)
        - Yield 的事件用 `dm_*` 前缀（与群聊 `agent_*` 区分）

    Args:
        target_agent_key: 目标 agent key（6 九洲一号群角色之一）。必须是 ROLES 里的合法 key。
        user_text: 用户私信文本。
        history: 兼容旧 Stage 6 DmMessage history;None 时从 AgentMemoryStore 读
        session_id: WS session id (用于 AgentMemoryStore 隔离)
        memory_store: 测试可注入;None 时走全局默认

    Yields:
        标准 DM 事件 dict:
            {"event": "dm_thinking", "agent", "name", "emoji"}
            {"event": "dm_msg_chunk", "agent", "chunk"}
            {"event": "dm_done", "agent", "name", "emoji", "full_text"}
            {"event": "dm_error", "message"}

    异常：
        - `ValueError`：target_agent_key 非法（不在 ROLES 里）
        - 其它 LLM 异常：被 try/except 兜住，yield {"event": "dm_error", ...}
    """
    if target_agent_key not in ROLES:
        yield {
            "event": "dm_error",
            "message": f"unknown target_agent: {target_agent_key!r}",
            "code": "UNKNOWN_AGENT",
        }
        return

    role = ROLES[target_agent_key]
    agent_name = role["name"]
    agent_emoji = role["emoji"]
    agent_provider = role.get("provider", "minimax")

    if memory_store is None:
        memory_store = get_agent_memory_store()

    # ----- 1) thinking event -----
    yield {
        "event": "dm_thinking",
        "agent": target_agent_key,
        "name": agent_name,
        "emoji": agent_emoji,
    }

    # ----- 2) build LLM input: system + history (DM only) + current user_text -----
    system_msg = SystemMessage(content=_wrap_persona(target_agent_key, role["system"]))

    # 把 history 转成 LangChain BaseMessage
    # Stage 7 Bug 2: 如果 history=None,从 AgentMemoryStore 读(包含 group + dm 统一时间线)
    history_msgs: list[BaseMessage] = []
    if history is not None:
        # 兼容旧 Stage 6 DmMessage history
        for m in history:
            if m.role == "user":
                history_msgs.append(HumanMessage(content=m.text))
            elif m.role == "agent":
                history_msgs.append(AIMessage(content=m.text, name=agent_name))
    else:
        # Stage 7 Bug 2: 从 AgentMemoryStore 读统一 memory,按 source 渲染前缀
        try:
            entries = memory_store.load_agent_memory(
                session_id=session_id, agent_key=target_agent_key,
            )
        except Exception as e:  # noqa: BLE001
            yield {
                "event": "dm_error",
                "message": f"load_agent_memory failed: {e}",
                "code": "MEMORY_LOAD_FAILED",
            }
            return

        for e in entries:
            if e.source == "group":
                # 群聊背景:加 [群聊背景] 前缀 + speaker name
                speaker_name = e.agent_name or e.speaker_key
                content = f"[群聊背景] {speaker_name}: {e.text}"
            else:
                # dm:user 原样,agent 加 name 前缀(让 LLM 知道说话者)
                content = e.text

            if e.role == "user":
                history_msgs.append(HumanMessage(content=content))
            elif e.role == "agent":
                history_msgs.append(AIMessage(content=content, name=e.agent_name or agent_name))

    # ws.py persists the current DM user event before entering this generator.
    # Avoid showing the same question twice to the model; direct callers that
    # have not persisted it still get the current user turn appended here.
    current_already_present = bool(
        history is None
        and entries
        and entries[-1].source == "dm"
        and entries[-1].role == "user"
        and entries[-1].text == user_text
    )
    if not current_already_present:
        history_msgs.append(HumanMessage(content=user_text))

    # ----- 3) call LLM with provider routing (Stage 7: Letta leaf if configured) -----
    full_text = ""
    all_msgs: list[BaseMessage] = [system_msg] + history_msgs

    if _use_letta_path(target_agent_key):
        # Stage 7: DM 走 Letta — 每个 target_agent 有独立持久 agent
        try:
            async for content_piece in _stream_with_generation_timeout(_stream_via_letta(
                role_key=target_agent_key,
                session_id=session_id,
                all_msgs=all_msgs,
            )):
                if not content_piece:
                    continue
                content_piece, capped = _cap_response_piece(full_text, content_piece)
                full_text += content_piece
                yield {
                    "event": "dm_msg_chunk",
                    "agent": target_agent_key,
                    "chunk": content_piece,
                }
                if capped:
                    break
        except Exception as letta_exc:  # noqa: BLE001
            # graceful degrade：退回 per-role provider
            yield {
                "event": "dm_error",
                "message": f"letta path failed, fallback to legacy: {letta_exc}",
                "code": "LETTA_FALLBACK",
            }
            try:
                llm = get_chat_model(provider=agent_provider)
                async for chunk in _stream_with_generation_timeout(llm.astream(all_msgs)):
                    content = (
                        chunk.content
                        if isinstance(chunk.content, str)
                        else str(chunk.content or "")
                    )
                    if not content:
                        continue
                    content, capped = _cap_response_piece(full_text, content)
                    full_text += content
                    yield {
                        "event": "dm_msg_chunk",
                        "agent": target_agent_key,
                        "chunk": content,
                    }
                    if capped:
                        break
            except Exception as e:  # noqa: BLE001
                yield {
                    "event": "dm_error",
                    "message": str(e),
                    "code": type(e).__name__,
                }
                full_text = _direct_response_fallback(target_agent_key)
                yield {
                    "event": "dm_msg_chunk",
                    "agent": target_agent_key,
                    "chunk": full_text,
                    "fallback": True,
                }
    else:
        # 路径 A：Stage 4-B per-role provider routing (4 minimax M3 + 2 agnes)
        try:
            llm = get_chat_model(provider=agent_provider)
            async for chunk in _stream_with_generation_timeout(llm.astream(all_msgs)):
                content = (
                    chunk.content
                    if isinstance(chunk.content, str)
                    else str(chunk.content or "")
                )
                if not content:
                    continue
                content, capped = _cap_response_piece(full_text, content)
                full_text += content
                yield {
                    "event": "dm_msg_chunk",
                    "agent": target_agent_key,
                    "chunk": content,
                }
                if capped:
                    break
        except Exception as e:  # noqa: BLE001
            yield {
                "event": "dm_error",
                "message": str(e),
                "code": type(e).__name__,
            }
            full_text = _direct_response_fallback(target_agent_key)
            yield {
                "event": "dm_msg_chunk",
                "agent": target_agent_key,
                "chunk": full_text,
                "fallback": True,
            }

    if not full_text:
        yield {
            "event": "dm_error",
            "message": "target role returned empty output; using direct-response fallback",
            "code": "EMPTY_GENERATION",
        }
        full_text = _direct_response_fallback(target_agent_key)
        yield {
            "event": "dm_msg_chunk",
            "agent": target_agent_key,
            "chunk": full_text,
            "fallback": True,
        }

    # ----- 4) 持久化 agent 回复到 AgentMemoryStore -----
    if full_text and memory_store is not None:
        try:
            memory_store.append_message(
                session_id=session_id,
                agent_key=target_agent_key,
                role="agent",
                source="dm",
                speaker_key=target_agent_key,
                text=full_text,
                agent_name=role["name"],
                agent_emoji=role["emoji"],
            )
        except Exception as e:  # noqa: BLE001
            # 持久化失败不能阻塞 dm_done 事件
            yield {"event": "memory_persist_warning", "stage": "dm_agent_append", "message": str(e)}

    # ----- 5) done event -----
    yield {
        "event": "dm_done",
        "agent": target_agent_key,
        "name": agent_name,
        "emoji": agent_emoji,
        "full_text": full_text,
    }
