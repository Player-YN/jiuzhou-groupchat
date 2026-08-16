from __future__ import annotations

import pytest

from app.behavior import (
    BehaviorEngine,
    BehaviorEvent,
    CandidateIntent,
    CandidatePolicy,
    DecisionLogStore,
)


def _event(text: str = "大家觉得怎么样？", **kwargs) -> BehaviorEvent:
    return BehaviorEvent(
        event_id=kwargs.pop("event_id", "evt-1"),
        session_id="s-1",
        event_type=kwargs.pop("event_type", "user_message"),
        text=text,
        **kwargs,
    )


def _intent(role_key: str, score: int = 3, **kwargs) -> CandidateIntent:
    values = {
        "relevance": score,
        "social_obligation": score,
        "relationship_motivation": score,
        "continuity": score,
        "persona_impulse": score,
        "novelty_potential": score,
        "proposed_action": "reply" if score else "silent",
        "contribution_key": f"idea:{role_key}" if score else "",
    }
    values.update(kwargs)
    return CandidateIntent(role_key=role_key, **values)


def test_natural_silence_when_no_candidate_crosses_threshold():
    decision = BehaviorEngine().decide(_event(), [_intent("shu-hang", 1)])
    assert decision.outcome == "silent"
    assert decision.selected_roles == []


def test_reaction_intent_is_not_promoted_to_full_reply():
    intent = _intent("shu-hang", 3, proposed_action="react")
    decision = BehaviorEngine().decide(_event(), [intent])
    assert decision.selected_roles == []
    score = next(item for item in decision.candidates if item.role_key == "shu-hang")
    assert score.proposed_action == "react"


def test_explicit_mention_has_deterministic_floor_without_llm_intent():
    decision = BehaviorEngine().decide(_event("@白前辈 在吗？"), [])
    assert decision.selected_roles == ["bai-qianbei"]
    score = next(item for item in decision.candidates if item.role_key == "bai-qianbei")
    assert score.final_score >= 0.90


def test_hard_mute_beats_explicit_mention():
    decision = BehaviorEngine().decide(
        _event("@白前辈 在吗？"),
        [],
        {"bai-qianbei": CandidatePolicy(muted=True)},
    )
    assert decision.selected_roles == []
    assert "muted" in next(
        item for item in decision.candidates if item.role_key == "bai-qianbei"
    ).reason_codes


def test_at_most_two_ordinary_responders_with_distinct_contributions():
    intents = [
        _intent("shu-hang", contribution_key="experience"),
        _intent("yao-shi", contribution_key="medical"),
        _intent("san-lang", contribution_key="risk"),
    ]
    decision = BehaviorEngine().decide(_event(), intents)
    assert decision.selected_roles == ["shu-hang", "yao-shi"]


def test_second_speaker_rejected_when_contribution_duplicates_first():
    intents = [
        _intent("shu-hang", contribution_key="same"),
        _intent("yao-shi", contribution_key="same"),
    ]
    assert BehaviorEngine().decide(_event(), intents).selected_roles == ["shu-hang"]


def test_recent_speaker_penalty_changes_winner_deterministically():
    intents = [_intent("shu-hang"), _intent("yao-shi")]
    policies = {"shu-hang": CandidatePolicy(recently_spoke=True)}
    decision = BehaviorEngine().decide(_event(), intents, policies)
    assert decision.selected_roles[0] == "yao-shi"


def test_cooldown_blocks_ordinary_but_not_explicit_mention():
    intent = _intent("shu-hang")
    policy = {"shu-hang": CandidatePolicy(cooldown_active=True)}
    assert BehaviorEngine().decide(_event(), [intent], policy).selected_roles == []
    mentioned = BehaviorEngine().decide(_event("@宋书航 回我"), [intent], policy)
    assert mentioned.selected_roles == ["shu-hang"]


def test_daily_budget_is_a_hard_gate():
    policy = {"shu-hang": CandidatePolicy(daily_count=5, daily_budget=5)}
    assert BehaviorEngine().decide(_event("@宋书航"), [], policy).selected_roles == []


def test_chain_stops_at_three_autonomous_hops():
    event = _event("继续聊", event_type="npc_message", speaker_key="yao-shi", chain_depth=3)
    decision = BehaviorEngine().decide(event, [_intent("shu-hang")])
    assert decision.outcome == "chain_stopped"
    assert decision.reason == "max_chain_depth_reached"


def test_score_is_replayable_for_identical_inputs():
    event = _event()
    intents = [_intent("shu-hang", 2), _intent("yao-shi", 3)]
    first = BehaviorEngine().decide(event, intents)
    second = BehaviorEngine().decide(event, intents)
    assert first.model_dump() == second.model_dump()


def test_decision_log_is_append_once_and_readable(tmp_path):
    store = DecisionLogStore(tmp_path / "decisions.sqlite")
    decision = BehaviorEngine().decide(_event(event_id="same-event"), [_intent("shu-hang")])
    assert store.save(decision) is True
    assert store.save(decision) is False
    assert store.matches_event(decision.event) is True
    collision = decision.event.model_copy(update={"text": "different"})
    assert store.matches_event(collision) is False
    loaded = store.get("same-event")
    assert loaded is not None
    assert loaded.model_dump() == decision.model_dump()
    matches, original, replayed = store.replay("same-event")
    assert matches is True
    assert original is not None and replayed is not None
    assert store.list_session("s-1")[0].event.event_id == "same-event"
    store.close()


@pytest.mark.asyncio
async def test_assessment_timeout_defaults_to_empty_intents(monkeypatch):
    import asyncio
    import app.llm as llm_module
    from app.behavior import assess_intents_detailed

    class HangingModel:
        async def ainvoke(self, messages):
            await asyncio.sleep(2)

    monkeypatch.setenv("BEHAVIOR_ASSESS_MODE", "llm")
    monkeypatch.setenv("BEHAVIOR_ASSESS_TIMEOUT_SEC", "0.01")
    monkeypatch.setattr(llm_module, "get_chat_model", lambda **kwargs: HangingModel())
    assessment = await assess_intents_detailed(_event(event_id="timeout"), [])
    assert assessment.candidates == []
    assert assessment.metadata.status == "timeout"
    assert assessment.metadata.prompt_hash


@pytest.mark.asyncio
async def test_assessment_rejects_duplicate_role_even_when_all_six_are_present(monkeypatch):
    import json
    from types import SimpleNamespace

    import app.llm as llm_module
    from app.behavior import assess_intents_detailed

    rows = [
        {"role_key": role_key, "proposed_action": "silent"}
        for role_key in (
            "shu-hang", "yao-shi", "san-lang", "bei-he", "bai-qianbei", "ling-die",
        )
    ]
    rows.append({"role_key": "shu-hang", "proposed_action": "reply"})

    class DuplicateModel:
        async def ainvoke(self, messages):
            return SimpleNamespace(content=json.dumps({"candidates": rows}))

    monkeypatch.setenv("BEHAVIOR_ASSESS_MODE", "llm")
    monkeypatch.setattr(llm_module, "get_chat_model", lambda **kwargs: DuplicateModel())
    assessment = await assess_intents_detailed(_event(event_id="duplicates"), [])
    assert assessment.candidates == []
    assert assessment.metadata.status == "invalid"
    assert assessment.metadata.error_code == "candidate_set_mismatch"


@pytest.mark.asyncio
async def test_default_assessment_is_heuristic_without_llm(monkeypatch):
    import app.llm as llm_module
    from app.behavior import assess_intents_detailed

    def boom(**kwargs):
        raise AssertionError("LLM must not be called in heuristic mode")

    monkeypatch.delenv("BEHAVIOR_ASSESS_MODE", raising=False)
    monkeypatch.setattr(llm_module, "get_chat_model", boom)
    assessment = await assess_intents_detailed(_event("@白前辈 在吗？"), [])
    assert assessment.metadata.status == "heuristic"
    assert assessment.metadata.latency_ms < 50
    bai = next(c for c in assessment.candidates if c.role_key == "bai-qianbei")
    assert bai.proposed_action == "reply"


def test_heuristic_user_message_usually_selects_speaker():
    from app.behavior import BehaviorEngine, heuristic_intents

    intents = heuristic_intents(_event("今天群里有点安静啊"))
    decision = BehaviorEngine().decide(_event("今天群里有点安静啊"), intents)
    assert decision.outcome == "respond"
    assert 1 <= len(decision.selected_roles) <= 2
    assert "shu-hang" in decision.selected_roles
