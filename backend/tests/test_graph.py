"""Unit tests for `app.graph._stream_via_letta` system-prompt + length-cap
prompt prefix behavior (Stage 7 P0 fix-up).

These tests pin the **defensive prompt-construction logic** in
`_stream_via_letta`: even when a NPC's Letta `core-memory` blocks are
empty / stale, the SystemMessage text still reaches the LLM as a
`【角色设定】...` prefix, and a `【回复要求】100-300字以内` line caps the
verbosity of the model (qwen2.5:1.5b + M3 both default to long-winded
paraphrases without an explicit instruction).

The tests mock the `LettaClient.stream_message` async generator so the
prompt text being POSTed can be inspected from the test side.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.messages import HumanMessage, SystemMessage

from app.llm import MockChatModel


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeLettaClient:
    """Minimal LettaClient stand-in that captures the prompt text POSTed.

    The real `LettaClient.stream_message` is replaced via monkey-patching
    in each test so we never hit a real Letta server.

    Implementation note: the real method is an `async def` with
    `yield ...` bodies (an async generator) — calling it returns the
    async generator directly, not a coroutine.  Our fake mirrors that
    shape so graph.py's `async for ev in client.stream_message(...)`
    works without an extra `await`.
    """

    def __init__(self, captured: list[str]) -> None:
        self._captured = captured
        # Pre-computed stream so the prompt-capture side-effect runs
        # BEFORE the first yield (async generators run lazily).
        self._chunks = [
            {"message_type": "assistant_message", "content": "收到。"},
            {"message_type": "assistant_message", "content": "在下同意。"},
        ]

    async def stream_message(self, agent_id: str, text: str):
        # The real call signature is `stream_message(agent_id, text)`;
        # the caller immediately does `async for ev in client.stream_message(...)`
        # which treats the return value as an async iterator.  We
        # therefore structure this as an async generator and stash
        # the captured prompt eagerly (side effects before the first
        # yield).
        self._captured.append(text)
        for chunk in self._chunks:
            yield chunk


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("role_key", "marker"),
    [
        ("shu-hang", "妈耶"),
        ("yao-shi", "老夫药师"),
        ("san-lang", "三浪就手痒"),
        ("bei-he", "老朽北河"),
        ("bai-qianbei", "嗯。善。"),
        ("ling-die", "妾身以为"),
    ],
)
async def test_mock_identity_anchor_selects_exact_character(role_key, marker):
    """Negative-role names inside the anchor must not hijack Mock identity."""
    from app.graph import ROLES, _wrap_persona

    model = MockChatModel(chunk_delay_ms=0)
    messages = [SystemMessage(content=_wrap_persona(role_key, ROLES[role_key]["system"]))]
    pieces = []
    async for chunk in model.astream(messages):
        pieces.append(str(chunk.content))
    assert marker in "".join(pieces)


@pytest.fixture
def fake_letta_captured() -> list[str]:
    """Each test gets a fresh prompt-capture list."""
    return []


@pytest.fixture
def stub_registry(monkeypatch):
    """Patch `get_or_create_agent_id` so the test never touches a real
    Letta server or SQLite registry.

    Returns the agent_id the fake registry hands out.
    """
    fake_aid = "agent-test-xyz123"
    fake_callable = AsyncMock(return_value=fake_aid)
    monkeypatch.setattr(
        "app.letta_bridge.get_or_create_agent_id", fake_callable,
    )
    return fake_callable


@pytest.mark.asyncio
async def test_stream_via_letta_prepends_system_prefix(
    monkeypatch,
    fake_letta_captured: list[str],
    stub_registry,
) -> None:
    """When a SystemMessage is present, the rendered Letta prompt MUST
    start with `【角色设定】...` containing that SystemMessage content,
    AND must end with a `【回复要求】100-300字以内` length-cap line, AND
    must include the human message somewhere in the body.
    """
    # Force Letta path: USE_LETTA=true, USE_MOCK_LLM=false
    monkeypatch.setenv("USE_LETTA", "true")
    monkeypatch.setenv("USE_MOCK_LLM", "false")
    monkeypatch.setenv("LETTA_BASE_URL", "http://letta-mock:8283")
    monkeypatch.setenv("LETTA_API_KEY", "letta_dev_password")
    monkeypatch.setenv("LETTA_LLM_MODEL", "minimax/MiniMax-M2.7-highspeed")
    # The `config.get_settings` is lru_cached — invalidate.
    from app.config import get_settings
    get_settings.cache_clear()

    fake = _FakeLettaClient(fake_letta_captured)

    # Replace `get_letta_client` with our fake so the body of
    # `_stream_via_letta` uses it.  The function does
    # `client = get_letta_client()`.
    fake_handle = MagicMock(return_value=fake)
    monkeypatch.setattr("app.letta_bridge.get_letta_client", fake_handle)

    try:
        # Re-import after monkeypatches to pick up fresh settings.
        from app.graph import _stream_via_letta  # noqa: PLC0415

        persona_text = (
            "你是宋书航，九洲一号群的萌新。\n"
            "口头禅：妈耶！\n"
            "说话风格：活泼、口语、带点新人紧张。"
        )
        msgs = [
            SystemMessage(content=persona_text),
            HumanMessage(content="大家好我是新来的"),
        ]

        pieces: list[str] = []
        async for piece in _stream_via_letta(
            role_key="shu-hang",
            session_id="test-sess-prefix",
            all_msgs=msgs,
        ):
            pieces.append(piece)

        # 1) Exactly one Letta POST; capture the rendered prompt.
        assert len(fake_letta_captured) == 1, (
            f"expected one Letta POST, got {len(fake_letta_captured)}"
        )
        prompt = fake_letta_captured[0]

        # 2) System prefix MUST show up at the top.
        assert prompt.startswith("【角色设定】"), (
            f"prompt must start with 【角色设定】 prefix, got: {prompt[:80]!r}"
        )
        assert "宋书航" in prompt
        assert "九洲一号群" in prompt
        assert persona_text in prompt, "persona text must be embedded in prompt"

        # 3) Length cap line MUST be present.
        assert "【回复要求】" in prompt
        assert "100-300 字" in prompt

        # 4) The user's HumanMessage content MUST still reach the LLM.
        assert "大家好我是新来的" in prompt

        # 5) The two streamed assistant chunks should have been yielded.
        assert "".join(pieces) == "收到。在下同意。"
    finally:
        # Reset cached settings so other tests aren't affected.
        get_settings.cache_clear()


@pytest.mark.asyncio
async def test_stream_via_letta_no_system_message(
    monkeypatch,
    fake_letta_captured: list[str],
    stub_registry,
) -> None:
    """If the caller hands in a zero-SystemMessage list (defensive
    safety net for malformed upstream callers), `_stream_via_letta`
    must still inject the `【回复要求】` cap line and must NOT raise.
    The `【角色设定】` prefix is suppressed because there's no system
    text to put inside it.
    """
    monkeypatch.setenv("USE_LETTA", "true")
    monkeypatch.setenv("USE_MOCK_LLM", "false")
    monkeypatch.setenv("LETTA_BASE_URL", "http://letta-mock:8283")
    monkeypatch.setenv("LETTA_API_KEY", "letta_dev_password")
    monkeypatch.setenv("LETTA_LLM_MODEL", "minimax/MiniMax-M2.7-highspeed")
    from app.config import get_settings
    get_settings.cache_clear()

    fake = _FakeLettaClient(fake_letta_captured)
    monkeypatch.setattr(
        "app.letta_bridge.get_letta_client", MagicMock(return_value=fake),
    )

    try:
        from app.graph import _stream_via_letta  # noqa: PLC0415

        msgs = [HumanMessage(content="test no system")]

        async for _ in _stream_via_letta(
            role_key="shu-hang",
            session_id="test-sess-nosystem",
            all_msgs=msgs,
        ):
            pass

        assert len(fake_letta_captured) == 1
        prompt = fake_letta_captured[0]
        # No system prefix when there's no system message.
        assert "【角色设定】" not in prompt, (
            f"unexpected 【角色设定】 when no SystemMessage: {prompt[:80]!r}"
        )
        # Length cap stays.
        assert "【回复要求】" in prompt
        assert "100-300 字" in prompt
        # User content still reaches the LLM.
        assert "test no system" in prompt
    finally:
        get_settings.cache_clear()


@pytest.mark.asyncio
async def test_stream_via_letta_concatenates_multiple_system_segments(
    monkeypatch,
    fake_letta_captured: list[str],
    stub_registry,
) -> None:
    """If multiple SystemMessage entries are passed (e.g. a plus a
    post-trim one), all of their content must be embedded in the
    prefix — concatenated in order.
    """
    monkeypatch.setenv("USE_LETTA", "true")
    monkeypatch.setenv("USE_MOCK_LLM", "false")
    monkeypatch.setenv("LETTA_BASE_URL", "http://letta-mock:8283")
    monkeypatch.setenv("LETTA_API_KEY", "letta_dev_password")
    monkeypatch.setenv("LETTA_LLM_MODEL", "minimax/MiniMax-M2.7-highspeed")
    from app.config import get_settings
    get_settings.cache_clear()

    fake = _FakeLettaClient(fake_letta_captured)
    monkeypatch.setattr(
        "app.letta_bridge.get_letta_client", MagicMock(return_value=fake),
    )

    try:
        from app.graph import _stream_via_letta  # noqa: PLC0415

        sys_a = "你是宋书航，说话活泼。"
        sys_b = "九洲一号群风格：道友之间互相@，语气亲切。"
        msgs = [
            SystemMessage(content=sys_a),
            HumanMessage(content="介绍一下你自己"),
            SystemMessage(content=sys_b),
        ]

        async for _ in _stream_via_letta(
            role_key="shu-hang",
            session_id="test-multi-sys",
            all_msgs=msgs,
        ):
            pass

        prompt = fake_letta_captured[0]
        assert prompt.startswith("【角色设定】")
        assert sys_a in prompt
        assert sys_b in prompt
        # Order preserved: sys_a appears before sys_b in the rendered prompt.
        assert prompt.index(sys_a) < prompt.index(sys_b)
        assert "【回复要求】" in prompt
        assert "介绍一下你自己" in prompt
    finally:
        get_settings.cache_clear()


@pytest.mark.asyncio
async def test_stream_via_letta_skip_empty_system_message(
    monkeypatch,
    fake_letta_captured: list[str],
    stub_registry,
) -> None:
    """An empty SystemMessage (whitespace only) must NOT add a 【角色设定】
    header, and the `【回复要求】` line must still be present."""
    monkeypatch.setenv("USE_LETTA", "true")
    monkeypatch.setenv("USE_MOCK_LLM", "false")
    monkeypatch.setenv("LETTA_BASE_URL", "http://letta-mock:8283")
    monkeypatch.setenv("LETTA_API_KEY", "letta_dev_password")
    monkeypatch.setenv("LETTA_LLM_MODEL", "minimax/MiniMax-M2.7-highspeed")
    from app.config import get_settings
    get_settings.cache_clear()

    fake = _FakeLettaClient(fake_letta_captured)
    monkeypatch.setattr(
        "app.letta_bridge.get_letta_client", MagicMock(return_value=fake),
    )

    try:
        from app.graph import _stream_via_letta  # noqa: PLC0415

        msgs = [
            SystemMessage(content="   \n  \n"),
            HumanMessage(content="hello"),
        ]

        async for _ in _stream_via_letta(
            role_key="shu-hang",
            session_id="test-empty-sys",
            all_msgs=msgs,
        ):
            pass

        prompt = fake_letta_captured[0]
        # Empty whitespace SystemMessage -> no 【角色设定】 header.
        assert "【角色设定】" not in prompt
        # Length cap stays.
        assert "【回复要求】" in prompt
        assert "hello" in prompt
    finally:
        get_settings.cache_clear()
