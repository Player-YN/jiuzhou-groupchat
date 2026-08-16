from __future__ import annotations

import uuid

import pytest

from app.behavior import CandidateIntent, IntentAssessment
from app.memory import AgentMemoryStore, set_agent_memory_store
from app.scheduler.behavior_coordinator import BehaviorCoordinator
from app.scheduler.connection_registry import ConnectionRegistry, set_connection_registry
from app.scheduler.group_semaphore import GroupChatSemaphore, set_group_semaphore
from app.scheduler.state import CronState


class _Socket:
    def __init__(self):
        self.messages = []

    async def send_json(self, message):
        self.messages.append(message)


class _FailingSocket:
    async def send_json(self, message):
        raise RuntimeError("socket closed")


@pytest.fixture
def coordinator_env(tmp_path):
    store = AgentMemoryStore(tmp_path / "memory.sqlite")
    registry = ConnectionRegistry()
    sem = GroupChatSemaphore(cooldown_seconds=0)
    old_store = set_agent_memory_store(store)
    old_registry = set_connection_registry(registry)
    old_sem = set_group_semaphore(sem)
    try:
        yield store, registry
    finally:
        set_agent_memory_store(old_store)
        set_connection_registry(old_registry)
        set_group_semaphore(old_sem)
        store.close()


def _intents(primary: str = "yao-shi"):
    result = []
    for role_key in ("shu-hang", "yao-shi", "san-lang", "bei-he", "bai-qianbei", "ling-die"):
        if role_key == primary:
            result.append(CandidateIntent(
                role_key=role_key,
                relevance=3,
                social_obligation=3,
                relationship_motivation=2,
                continuity=3,
                persona_impulse=2,
                novelty_potential=3,
                proposed_action="reply",
                contribution_key="new-angle",
            ))
        else:
            result.append(CandidateIntent(role_key=role_key))
    return result


@pytest.mark.asyncio
async def test_one_idle_event_uses_one_batch_and_pushes_one_speaker(
    monkeypatch, coordinator_env,
):
    store, registry = coordinator_env
    socket_a, socket_b = _Socket(), _Socket()
    registry.register("session-a", socket_a)
    registry.register("session-b", socket_b)
    calls = 0

    async def fake_assess(event, context):
        nonlocal calls
        calls += 1
        return IntentAssessment(candidates=_intents())

    async def fake_stream(**kwargs):
        yield "药"
        yield "到"

    monkeypatch.setattr(
        "app.scheduler.behavior_coordinator.assess_intents_detailed", fake_assess,
    )
    monkeypatch.setattr("app.scheduler.behavior_coordinator._use_letta_path", lambda role_key: True)
    monkeypatch.setattr(
        "app.scheduler.behavior_coordinator.stream_via_letta_with_retry", fake_stream,
    )

    coordinator = BehaviorCoordinator(state=CronState(), daily_budget=2)
    result = await coordinator.trigger(
        "idle_tick",
        text="群里安静了一会儿",
        event_id=f"test-{uuid.uuid4()}",
    )

    assert calls == 1
    assert result["event"] == "pushed"
    assert result["role_key"] == "yao-shi"
    assert len(socket_a.messages) == len(socket_b.messages) == 1
    assert socket_a.messages[0]["payload"]["source"] == "behavior_coordinator"
    assert any(item.text == "药到" for item in store.load_agent_memory("session-a", "shu-hang"))


@pytest.mark.asyncio
async def test_failed_semantic_assessment_defaults_to_silence(monkeypatch, coordinator_env):
    _, registry = coordinator_env
    registry.register("online", _Socket())
    async def failed_assess(event, context):
        return IntentAssessment()

    monkeypatch.setattr(
        "app.scheduler.behavior_coordinator.assess_intents_detailed", failed_assess,
    )
    coordinator = BehaviorCoordinator(state=CronState())
    result = await coordinator.trigger(
        "world_event",
        text="远处似乎有动静",
        event_id=f"test-{uuid.uuid4()}",
    )
    assert result["event"] == "silent"
    assert coordinator.silent_events == 1


@pytest.mark.asyncio
async def test_disabled_or_offline_coordinator_skips_before_assessment(
    monkeypatch, coordinator_env,
):
    calls = 0

    async def should_not_run(event, context):
        nonlocal calls
        calls += 1
        return IntentAssessment(candidates=_intents())

    monkeypatch.setattr(
        "app.scheduler.behavior_coordinator.assess_intents_detailed", should_not_run,
    )
    disabled = BehaviorCoordinator(state=CronState(enabled=False))
    result = await disabled.trigger("idle_tick", text="idle")
    assert result["reason"] == "disabled"

    offline = BehaviorCoordinator(state=CronState(enabled=True))
    result = await offline.trigger("idle_tick", text="idle")
    assert result["reason"] == "no_active_sessions"
    assert calls == 0


@pytest.mark.asyncio
async def test_npc_filter_is_applied_as_hard_mute(monkeypatch, coordinator_env):
    _, registry = coordinator_env
    registry.register("online", _Socket())

    async def filtered_primary(event, context):
        return IntentAssessment(candidates=_intents(primary="yao-shi"))

    monkeypatch.setattr(
        "app.scheduler.behavior_coordinator.assess_intents_detailed", filtered_primary,
    )
    coordinator = BehaviorCoordinator(state=CronState(npc_filter=["shu-hang"]))
    result = await coordinator.trigger("idle_tick", text="idle")
    assert result["event"] == "silent"


@pytest.mark.asyncio
async def test_daily_budget_prevents_second_proactive_generation(monkeypatch, coordinator_env):
    _, registry = coordinator_env
    registry.register("online", _Socket())

    async def primary(event, context):
        return IntentAssessment(candidates=_intents(primary="yao-shi"))

    stream_calls = 0

    async def fake_stream(**kwargs):
        nonlocal stream_calls
        stream_calls += 1
        yield "一次"

    state = CronState()
    coordinator = BehaviorCoordinator(state=state, daily_budget=1)
    monkeypatch.setattr(
        "app.scheduler.behavior_coordinator.assess_intents_detailed", primary,
    )
    monkeypatch.setattr("app.scheduler.behavior_coordinator._use_letta_path", lambda role_key: True)
    monkeypatch.setattr(
        "app.scheduler.behavior_coordinator.stream_via_letta_with_retry", fake_stream,
    )

    first = await coordinator.trigger("idle_tick", text="first")
    state.last_fire_at.clear()  # isolate the daily-budget gate from cooldown
    second = await coordinator.trigger("idle_tick", text="second")
    assert first["event"] == "pushed"
    assert second["event"] == "silent"
    assert stream_calls == 1


@pytest.mark.asyncio
async def test_one_broken_socket_does_not_abort_other_pushes(monkeypatch, coordinator_env):
    _, registry = coordinator_env
    good = _Socket()
    registry.register("broken", _FailingSocket())
    registry.register("good", good)

    async def primary(event, context):
        return IntentAssessment(candidates=_intents(primary="yao-shi"))

    async def fake_stream(**kwargs):
        yield "仍可送达"

    monkeypatch.setattr(
        "app.scheduler.behavior_coordinator.assess_intents_detailed", primary,
    )
    monkeypatch.setattr("app.scheduler.behavior_coordinator._use_letta_path", lambda role_key: True)
    monkeypatch.setattr(
        "app.scheduler.behavior_coordinator.stream_via_letta_with_retry", fake_stream,
    )
    result = await BehaviorCoordinator(state=CronState()).trigger("idle_tick", text="idle")
    assert result["event"] == "pushed"
    assert result["sessions"] == 1
    assert len(good.messages) == 1


@pytest.mark.asyncio
async def test_generation_exception_returns_error_without_killing_coordinator(
    monkeypatch, coordinator_env,
):
    _, registry = coordinator_env
    registry.register("online", _Socket())

    async def primary(event, context):
        return IntentAssessment(candidates=_intents(primary="yao-shi"))

    class FailingModel:
        async def astream(self, messages):
            if False:
                yield None
            raise RuntimeError("provider down")

    monkeypatch.setattr(
        "app.scheduler.behavior_coordinator.assess_intents_detailed", primary,
    )
    monkeypatch.setattr("app.scheduler.behavior_coordinator._use_letta_path", lambda role_key: False)
    monkeypatch.setattr(
        "app.scheduler.behavior_coordinator.get_chat_model",
        lambda **kwargs: FailingModel(),
    )
    coordinator = BehaviorCoordinator(state=CronState())
    result = await coordinator.trigger("idle_tick", text="idle")
    assert result["event"] == "error"
    assert result["reason"] == "generation_failed"
    assert coordinator.last_error == "RuntimeError: provider down"
