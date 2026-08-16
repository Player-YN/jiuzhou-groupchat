from __future__ import annotations

import pytest

from app.behavior import CandidateIntent, DecisionLogStore, IntentAssessment
from app.llm import MockChatModel
from app.memory import AgentMemoryStore


def _intent(role_key: str, contribution: str) -> CandidateIntent:
    return CandidateIntent(
        role_key=role_key,
        relevance=3,
        social_obligation=3,
        relationship_motivation=2,
        continuity=3,
        persona_impulse=2,
        novelty_potential=3,
        proposed_action="reply",
        contribution_key=contribution,
    )


@pytest.fixture
def group_runtime(monkeypatch, tmp_path):
    store = AgentMemoryStore(tmp_path / "memory.sqlite")
    log = DecisionLogStore(tmp_path / "decisions.sqlite")
    model = MockChatModel(chunk_delay_ms=0)
    monkeypatch.setattr("app.graph._use_letta_path", lambda role_key: False)
    monkeypatch.setattr("app.graph.get_chat_model", lambda *args, **kwargs: model)
    try:
        yield store, log
    finally:
        store.close()
        log.close()


@pytest.mark.asyncio
async def test_ordinary_message_can_end_in_natural_silence(monkeypatch, group_runtime):
    from app.graph import stream_group_chat

    store, log = group_runtime

    async def no_intent(event, context):
        return IntentAssessment()

    monkeypatch.setattr("app.graph.assess_intents_detailed", no_intent)
    events = [event async for event in stream_group_chat(
        "嗯。", session_id="silent", event_id="silent-1",
        memory_store=store, decision_log=log,
    )]
    assert not any(event["event"] == "agent_done" for event in events)
    done = next(event for event in events if event["event"] == "group_chat_done")
    assert done["rounds"] == 0
    assert done["outcome"] == "silent"


@pytest.mark.asyncio
async def test_batch_arbitration_generates_no_more_than_two_roles(monkeypatch, group_runtime):
    from app.graph import stream_group_chat

    store, log = group_runtime

    async def three_strong(event, context):
        return IntentAssessment(candidates=[
            _intent("shu-hang", "experience"),
            _intent("yao-shi", "medical"),
            _intent("san-lang", "risk"),
        ])

    monkeypatch.setattr("app.graph.assess_intents_detailed", three_strong)
    events = [event async for event in stream_group_chat(
        "这件事怎么办？", session_id="two", event_id="two-1", max_rounds=8,
        memory_store=store, decision_log=log,
    )]
    done_events = [event for event in events if event["event"] == "agent_done"]
    assert [event["agent"] for event in done_events] == ["shu-hang", "yao-shi"]
    assert next(event for event in events if event["event"] == "group_chat_done")["rounds"] == 2


@pytest.mark.asyncio
async def test_explicit_mention_survives_failed_assessment(monkeypatch, group_runtime):
    from app.graph import stream_group_chat

    store, log = group_runtime

    async def invalid_output(event, context):
        return IntentAssessment()

    monkeypatch.setattr("app.graph.assess_intents_detailed", invalid_output)
    events = [event async for event in stream_group_chat(
        "@白前辈 你在吗？", session_id="mention", event_id="mention-1",
        memory_store=store, decision_log=log,
    )]
    assert [event["agent"] for event in events if event["event"] == "agent_done"] == [
        "bai-qianbei"
    ]
    done = next(event for event in events if event["event"] == "agent_done")
    assert "嗯。善。" in done["full_text"]
    assert "我是宋书航" not in done["full_text"]


@pytest.mark.asyncio
async def test_same_event_id_is_suppressed_before_duplicate_memory(monkeypatch, group_runtime):
    from app.graph import stream_group_chat

    store, log = group_runtime

    async def one_intent(event, context):
        return IntentAssessment(candidates=[_intent("shu-hang", "answer")])

    monkeypatch.setattr("app.graph.assess_intents_detailed", one_intent)
    first = [event async for event in stream_group_chat(
        "第一次", session_id="idem", event_id="same-id",
        memory_store=store, decision_log=log,
    )]
    before = store.count_messages("idem", "shu-hang")
    second = [event async for event in stream_group_chat(
        "第一次", session_id="idem", event_id="same-id",
        memory_store=store, decision_log=log,
    )]
    after = store.count_messages("idem", "shu-hang")

    assert any(event["event"] == "agent_done" for event in first)
    assert second[0]["event"] == "behavior_duplicate"
    assert after == before


@pytest.mark.asyncio
async def test_explicit_mention_gets_persona_fallback_when_generation_fails(
    monkeypatch, group_runtime,
):
    import app.graph as graph_module

    store, log = group_runtime

    async def no_semantics(event, context):
        return IntentAssessment()

    class FailingModel:
        async def astream(self, messages):
            if False:
                yield None
            raise RuntimeError("provider unavailable")

    monkeypatch.setattr(graph_module, "assess_intents_detailed", no_semantics)
    monkeypatch.setattr(graph_module, "get_chat_model", lambda **kwargs: FailingModel())
    events = [event async for event in graph_module.stream_group_chat(
        "@白前辈 在吗？",
        session_id="fallback",
        event_id="fallback-1",
        memory_store=store,
        decision_log=log,
    )]
    done = next(event for event in events if event["event"] == "agent_done")
    assert done["agent"] == "bai-qianbei"
    assert done["full_text"] == "嗯。我在。"
    assert any(event.get("fallback") is True for event in events)


@pytest.mark.asyncio
async def test_generated_reply_is_hard_capped_even_if_model_ignores_prompt(
    monkeypatch, group_runtime,
):
    import app.graph as graph_module
    from langchain_core.messages import AIMessageChunk

    store, log = group_runtime

    async def selected(event, context):
        return IntentAssessment(candidates=[_intent("shu-hang", "answer")])

    class VerboseModel:
        async def astream(self, messages):
            yield AIMessageChunk(content="长" * 200)

    monkeypatch.setenv("BEHAVIOR_MAX_RESPONSE_CHARS", "40")
    monkeypatch.setattr(graph_module, "assess_intents_detailed", selected)
    monkeypatch.setattr(graph_module, "get_chat_model", lambda **kwargs: VerboseModel())
    events = [event async for event in graph_module.stream_group_chat(
        "请回答",
        session_id="capped",
        event_id="capped-1",
        memory_store=store,
        decision_log=log,
    )]
    done = next(event for event in events if event["event"] == "agent_done")
    assert done["full_text"] == "长" * 40


@pytest.mark.asyncio
async def test_direct_mention_timeout_uses_fallback(monkeypatch, group_runtime):
    import asyncio
    import app.graph as graph_module

    store, log = group_runtime

    async def no_semantics(event, context):
        return IntentAssessment()

    class HangingModel:
        async def astream(self, messages):
            await asyncio.sleep(1)
            yield None

    monkeypatch.setattr(graph_module, "assess_intents_detailed", no_semantics)
    monkeypatch.setattr(graph_module, "get_chat_model", lambda **kwargs: HangingModel())
    monkeypatch.setattr(graph_module, "_generation_timeout_seconds", lambda: 0.01)
    events = [event async for event in graph_module.stream_group_chat(
        "@白前辈 在吗？",
        session_id="timeout-fallback",
        event_id="timeout-fallback-1",
        memory_store=store,
        decision_log=log,
    )]
    assert any(event["event"] == "error" for event in events)
    done = next(event for event in events if event["event"] == "agent_done")
    assert done["full_text"] == "嗯。我在。"
