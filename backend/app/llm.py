"""LLM 工厂：依据配置返回 ChatModel 实例（OpenAI / Anthropic / Ollama / Mock）。

Mock 实现：逐字 yield ChatGenerationChunk（LangChain 内部 wrap 成 AIMessageChunk），
用于离线/Eval gate 演示。
"""
from __future__ import annotations

import asyncio
import re
from typing import AsyncIterator

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk
from langchain_core.outputs import ChatGenerationChunk, ChatResult
from pydantic import ConfigDict

from app.config import get_settings


# ===== Mock LLM — 用于离线演示和 Eval gate =====
class MockChatModel(BaseChatModel):
    """离线 Mock。根据 system prompt 推断角色，生成角色化回复。

    触发 Mock 的条件：
        1. USE_MOCK_LLM=true（强制）
        2. 没有任何 API key（兜底，避免启动即报错）

    实现要点：_astream yield ChatGenerationChunk（不是 AIMessageChunk），
    LangChain 内部会正确 wrap 成 AIMessageChunk 并填充所有元数据（message /
    generation_info / usage_metadata 等），避免下游访问属性 AttributeError。
    """

    reply: str = (
        "你好！我是 Project B 的 Mock 助手。这是一条模拟流式回复："
        "我会逐字 yield 每个 token，前端会看到'打字机'效果。"
        "当你配置了真实的 OPENAI_API_KEY 后，本服务会自动切换到 OpenAI GPT 模型。"
    )

    # 角色化回复模板 (system prompt 关键词 → reply)
    # Stage 4-B：九洲一号群聊天群 6 角色 (关键词用中文名/口头禅，命中第一条匹配)
    ROLE_REPLIES: dict[str, str] = {
        # 1. 宋书航 (shu-hang, minimax M3)
        "宋书航": (
            "🌟 妈耶！大家好我是宋书航，九洲一号群新人+1！"
            "前辈这问题在下也想插嘴——感觉有坑但又手痒..."
            "等等，让在下先抖三抖再说！"
        ),
        # 2. 药师 (yao-shi, minimax M3)
        "药师": (
            "💊 老夫药师，先把把脉。嗯，此话题火候尚欠。"
            "依老夫看，需三味主药：1) 问清楚病灶 2) 验明副作用 3) 留个退路。"
            "莫急，让老夫再望闻问切一轮。"
        ),
        # 3. 狂刀三浪 (san-lang, minimax M3)
        "狂刀三浪": (
            "🗡️ 哈！一听到这话题三浪就手痒！这波我上！"
            "管他什么深谋远虑，赤血狂刀一刀斩之！痛快！"
            "药师你别念叨了，让爷先冲为敬！"
        ),
        # 4. 北河散人 (bei-he, agnes)
        "北河散人": (
            "🌊 老朽北河，听诸位高论，倒想起一句古话：谋定而后动。"
            "依老朽之见，此事当徐徐图之，不可一蹴而就。"
            "后生莫急，且听老朽把这脉络理一理。"
        ),
        # 5. 白前辈 (bai-qianbei, agnes)
        "白前辈": "👻 嗯。善。此事有趣。",
        # 6. 灵蝶尊者 (ling-die, minimax M3)
        "灵蝶尊者": (
            "🦋 妾身以为，此事蹊跷。诸位莫要妄动。"
            "如蝶恋花，急则折翼，缓则破茧。"
            "本尊记下了，且让妾身再细察一番。"
        ),
    }

    chunk_delay_ms: int = 60  # 每字延迟，便于肉眼看到流式

    model_config = ConfigDict(arbitrary_types_allowed=True)

    @property
    def _llm_type(self) -> str:
        return "mock-chat"

    def _generate(self, messages, stop=None, **kwargs) -> ChatResult:
        """非流式调用（LangChain 默认 fallback）。"""
        return ChatResult(
            generations=[
                ChatGenerationChunk(
                    message=AIMessage(content=self.reply)
                )
            ]
        )

    async def _astream(
        self, messages, stop=None, **kwargs
    ) -> AsyncIterator[ChatGenerationChunk]:
        """流式调用：根据 system prompt 选角色化回复，逐字 yield ChatGenerationChunk。"""
        # Prefer the explicit identity anchor. A raw keyword scan is unsafe:
        # the anchor also lists other roles in the "you are not X" sentence,
        # which previously made every NPC match 宋书航 first.
        reply_text = self.reply
        for m in messages:
            content = m.content if hasattr(m, "content") else str(m)
            anchor = re.search(r"唯一扮演的角色是【([^】]+)】", str(content))
            if anchor and anchor.group(1) in self.ROLE_REPLIES:
                reply_text = self.ROLE_REPLIES[anchor.group(1)]
                break
        if reply_text == self.reply:
            # Compatibility for prompts created before identity anchors.
            for m in messages:
                content = m.content if hasattr(m, "content") else str(m)
                exact = re.search(r"你是【([^】]+)】", str(content))
                if exact and exact.group(1) in self.ROLE_REPLIES:
                    reply_text = self.ROLE_REPLIES[exact.group(1)]
                    break
        if reply_text == self.reply:
            combined = "\n".join(
                str(m.content if hasattr(m, "content") else m) for m in messages
            )
            for keyword, role_reply in self.ROLE_REPLIES.items():
                if keyword in combined:
                    reply_text = role_reply
                    break

        for i, ch in enumerate(reply_text):
            await asyncio.sleep(self.chunk_delay_ms / 1000)
            yield ChatGenerationChunk(
                message=AIMessageChunk(content=ch, id=f"mock-{i}"),
                generation_info={"mock_chunk_index": i},
            )


def get_chat_model(temperature: float = 0.7, provider: str | None = None) -> BaseChatModel:
    """依据环境配置 / 角色 provider 偏好返回合适的 ChatModel.

    Stage 4-B 起支持 per-role provider routing:
        - provider="minimax" → 九洲一号群 4 便宜角色, 用 M3 模型 (Stage 4-A 默认)
        - provider="agnes"   → 九洲一号群 2 高质量角色, 用 agnes-2.0-flash
        - provider=None      → 走 s.active_provider (默认全局)

    Fallback 策略:
        - USE_MOCK_LLM=true 强 mock 优先, 任何 provider 都被压成 mock
        - 角色级 provider 缺 key → 自动回退到 s.active_provider (避免单 key 环境崩溃)

    Precedence: USE_MOCK_LLM > provider (per-role) > active_provider
    """
    s = get_settings()

    # 0) 强 mock 优先 (USE_MOCK_LLM=true 覆盖一切 per-role 偏好, 用于离线 smoke)
    if s.use_mock_llm:
        return MockChatModel()

    # 1) 解析目标 provider (None → 全局默认)
    target = provider or s.active_provider

    # 2) 角色级 provider 缺 key → 兜底
    if target == "minimax" and not s.minimax_api_key:
        target = s.active_provider
    if target == "agnes" and not s.agnes_api_key:
        target = s.active_provider

    # 3) 按 target 分发 (下面所有分支把 'provider' 替换为 'target')
    if target == "mock":
        return MockChatModel()

    if target == "openai":
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            model=s.openai_model,
            temperature=temperature,
            api_key=s.openai_api_key,
            base_url=s.openai_base_url,
            streaming=True,
        )

    if target == "anthropic":
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(
            model=s.anthropic_model,
            temperature=temperature,
            api_key=s.anthropic_api_key,
            streaming=True,
        )

    if target == "deepseek":
        from langchain_openai import ChatOpenAI  # DeepSeek 兼容 OpenAI 协议

        return ChatOpenAI(
            model=s.deepseek_model,
            temperature=temperature,
            api_key=s.deepseek_api_key,
            base_url="https://api.deepseek.com/v1",
            streaming=True,
        )

    if target == "minimax":
        # MiniMax：OpenAI 兼容协议（Token Plan: sk-cp-* prefix）
        # 端点：https://api.minimaxi.com/v1  （chat/completions 子路径 LangChain 自动追加）
        #
        # Stage 4-A 起默认切到 M3 模型（minimax_m3_model，default "MiniMax-M3"）。
        #
        # 关键差异（来自 MiniMax 官方 API 文档）：
        #   - M3 支持 `thinking: {"type": "disabled"}` 真关：响应 content 字段直接是纯文本，
        #     没有 <think> 标签，也没有 reasoning_content/reasoning_details。
        #   - M2.x 不支持关闭 thinking（"对于 M2.x 模型，thinking 无法关闭"），只能靠
        #     `reasoning_split: True` 把 thinking 内容拆到 reasoning_content 字段，
        #     content 里仍会残留 <think>...</think> 标签。
        #
        # 因此我们按模型名区分：
        #   - 模型名含 "M3"（不区分大小写）→ 传 thinking.disabled 真关
        #   - 否则（M2.x 兜底）→ 不传 thinking，仅传 reasoning_split 拆字段
        #
        # 参考：https://platform.minimaxi.com/docs/api-reference/text-openai-api
        #   - "对于 MiniMax-M3，`thinking` 参数用于控制模型是否可以输出 thinking 内容"
        #   - "设置 thinking: {"type": "disabled"} 可跳过 thinking 并直接回答"
        #   - "对于 M2.x 模型，thinking 无法关闭"
        from langchain_openai import ChatOpenAI

        model_name = s.minimax_m3_model  # Stage 4-A: 默认 MiniMax-M3
        extra_body: dict = {"reasoning_split": True}
        # M3 真支持 thinking disable；M2.x 不支持，传了也无害（doc 说 silently ignored）
        # 但为避免对 M2.x override 路径造成困惑，仅在确认是 M3 时才传。
        if "m3" in model_name.lower():
            extra_body["thinking"] = {"type": "disabled"}

        return ChatOpenAI(
            model=model_name,
            temperature=temperature,
            api_key=s.minimax_api_key,
            base_url=s.minimax_base_url,
            streaming=True,
            extra_body=extra_body,
        )

    if target == "agnes":
        # Agnes AI：OpenAI 兼容协议
        # 端点：https://apihub.agnes-ai.com/v1
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            model=s.agnes_model,
            temperature=temperature,
            api_key=s.agnes_api_key,
            base_url=s.agnes_base_url,
            streaming=True,
        )

    raise RuntimeError(f"未知 provider: {target}")
