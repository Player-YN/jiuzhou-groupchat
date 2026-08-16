"""Tests for `app.scheduler.trigger_scoring` — P0-B per-message trigger scoring.

These tests use only stdlib + the trigger_scoring module under test; no real
Letta / DB / LLM.  `MockMessage` duck-types `AgentMemoryEntry` via attribute
access (the production code uses `getattr` everywhere instead of `isinstance`,
so the same path is exercised).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from app.scheduler.trigger_scoring import (
    TriggerScore,
    TriggerScorer,
    TriggerWeights,
    derive_persona_keywords,
)


# ----------------------------------------------------------------------------
# Mocks
# ----------------------------------------------------------------------------


@dataclass
class MockMessage:
    """Duck-type stand-in for `AgentMemoryEntry` (Pydantic model).

    The production scoring code only reads 4 fields via `getattr`:
    `text`, `speaker_key`, `timestamp`, `role`.  Keeping the mock minimal
    means changes to `AgentMemoryEntry`'s other fields don't break tests.
    """

    text: str
    speaker_key: str
    timestamp: int  # ms (matches AgentMemoryEntry.timestamp unit)
    role: str = "agent"  # "user" or "agent"


# Fixed `ts` for reproducibility across tests — epoch ms.
T0_MS: int = 1_700_000_000_000


def _msg(text: str, speaker_key: str, ts: float = T0_MS, role: str = "agent") -> dict:
    """Construct a `score_message` msg dict."""
    return {"text": text, "speaker_key": speaker_key, "ts": ts}


def _kw(*kws: str) -> list[str]:
    """Helper: build a persona_keywords list."""
    return list(kws)


# 6 九洲一号群 role_keys + a small persona keyword set each, sufficient to
# exercise condition_reinforcement + topic_match.  Multi-char entries
# (e.g. "白前辈", "赤血", "九转") test that exact-set-membership intersection
# works for non-CJK word tokens too.
PERSONA_KEYWORDS: dict[str, list[str]] = {
    "shu-hang": _kw("宋", "书", "航", "妈耶", "吐槽", "九洲", "现代梗"),
    "yao-shi": _kw("药", "师", "丹", "方", "九转", "冰火", "炼丹"),
    "san-lang": _kw("刀", "浪", "赤血", "三浪", "爽快", "六品"),
    "bei-he": _kw("北河", "散人", "老朽", "水系", "八品"),
    "bai-qianbei": _kw("白", "前辈", "善", "可", "有趣", "九品"),
    "ling-die": _kw("灵", "蝶", "蝴蝶", "灵蝶岛", "灵蝶尊者"),
}

ALL_ROLES: list[str] = [
    "shu-hang",
    "yao-shi",
    "san-lang",
    "bei-he",
    "bai-qianbei",
    "ling-die",
]


def _scores_for(scorer: TriggerScorer, msg: dict, roles: list[str]) -> list[TriggerScore]:
    """Convenience: score `msg` against `roles` with no recent events and the
    canonical persona_keywords dict.  Tests that need recent events build
    their own."""
    return scorer.score_message(msg, roles, [], PERSONA_KEYWORDS)


# ----------------------------------------------------------------------------
# uncertainty (u)
# ----------------------------------------------------------------------------


def test_uncertainty_high_when_topic_new() -> None:
    """NPC has not spoken recently → u == 1.0.

    The recent_events list contains only a `user` event, so the NPC's own
    utterance set is empty, and uncertainty should max out.
    """
    scorer = TriggerScorer()
    msg = _msg("Hello world, this is a fresh new topic.", speaker_key="user")
    recent = [
        MockMessage(
            text="user said something unrelated",
            speaker_key="user",
            timestamp=T0_MS - 60_000,
            role="user",
        ),
    ]
    scores = scorer.score_message(msg, ["shu-hang"], recent, PERSONA_KEYWORDS)
    assert len(scores) == 1
    assert scores[0].role_key == "shu-hang"
    assert scores[0].breakdown["u"] == pytest.approx(1.0)


def test_uncertainty_zero_when_overlap() -> None:
    """Msg tokens overlap with NPC's own recent events → u == 0.0 (binary form).

    `yao-shi` recently said "药方" in a recent event; the new msg also
    contains "药" and "方" → binary u drops to 0.0.
    """
    scorer = TriggerScorer()
    msg = _msg("药方 丹道 炼丹", speaker_key="user")
    recent = [
        MockMessage(
            text="我来开个 药方 给你 吃吃",
            speaker_key="yao-shi",
            timestamp=T0_MS - 30_000,
            role="agent",
        ),
    ]
    scores = scorer.score_message(msg, ["yao-shi"], recent, PERSONA_KEYWORDS)
    assert scores[0].breakdown["u"] == pytest.approx(0.0)


# ----------------------------------------------------------------------------
# condition_reinforcement (r)
# ----------------------------------------------------------------------------


def test_condition_reinforcement_when_mentioned() -> None:
    """`@shu-hang` substring in msg → r == 1.0 for shu-hang, 0.0 for others."""
    scorer = TriggerScorer()
    msg = _msg("hey @shu-hang 你怎么看？", speaker_key="user")
    scores = _scores_for(scorer, msg, ALL_ROLES)

    sh = next(s for s in scores if s.role_key == "shu-hang")
    assert sh.breakdown["r"] == 1.0

    # Every other NPC's `r` should be 0.0 — they weren't @-mentioned and
    # none of their persona keywords (e.g. "药", "刀", "北河") appear in
    # the msg tokens.
    for s in scores:
        if s.role_key == "shu-hang":
            continue
        assert s.breakdown["r"] == 0.0, f"{s.role_key} unexpectedly got r=1.0"


# ----------------------------------------------------------------------------
# cost (c)
# ----------------------------------------------------------------------------


def test_cost_high_when_recently_spoke() -> None:
    """shu-hang spoke 1 min ago → c == 1.0 for shu-hang (direct self-cost)."""
    scorer = TriggerScorer()
    msg = _msg("continue the conversation", speaker_key="user")
    recent = [
        MockMessage(
            text="妈耶啊啊啊",
            speaker_key="shu-hang",
            timestamp=T0_MS - 60_000,  # 1 min ago
            role="agent",
        ),
    ]
    scores = scorer.score_message(msg, ["shu-hang"], recent, PERSONA_KEYWORDS)
    assert scores[0].breakdown["c"] == 1.0


def test_cost_bump_from_crowd_replies() -> None:
    """≥ 3 distinct NPCs replied in the last 2 min → c bumped to 0.5
    even if the target NPC itself did NOT speak.
    """
    scorer = TriggerScorer()
    msg = _msg("continue the conversation", speaker_key="user")
    recent = [
        MockMessage(text="a", speaker_key="shu-hang", timestamp=T0_MS - 30_000, role="agent"),
        MockMessage(text="b", speaker_key="yao-shi", timestamp=T0_MS - 20_000, role="agent"),
        MockMessage(text="c", speaker_key="san-lang", timestamp=T0_MS - 10_000, role="agent"),
    ]
    # Target = bei-he (did not speak in recent events)
    scores = scorer.score_message(msg, ["bei-he"], recent, PERSONA_KEYWORDS)
    assert scores[0].breakdown["c"] == pytest.approx(0.5)


# ----------------------------------------------------------------------------
# score_message (general)
# ----------------------------------------------------------------------------


def test_score_message_returns_sorted_desc() -> None:
    """Returned list is non-increasing in `score`.

    Uses a message that ONLY triggers shu-hang's @-mention (no other NPC's
    persona keywords appear in the msg tokens), so the expected ordering
    is unambiguous: shu-hang wins via `r=1.0`, others all sit at `0.4`.
    """
    scorer = TriggerScorer()
    msg = _msg("@shu-hang hello there", speaker_key="user")
    scores = scorer.score_message(
        msg, ["san-lang", "shu-hang", "yao-shi"], [], PERSONA_KEYWORDS,
    )
    # Sorted desc property — the headline invariant
    for i in range(len(scores) - 1):
        assert scores[i].score >= scores[i + 1].score, (
            f"index {i}: {scores[i].role_key}={scores[i].score} should be >= "
            f"{scores[i + 1].role_key}={scores[i + 1].score}"
        )
    # Sanity: shu-hang should win given the @-mention (and nothing else
    # triggers other NPCs' condition_reinforcement or topic_match).
    assert scores[0].role_key == "shu-hang"
    assert scores[0].score == pytest.approx(0.7)  # 0.4*u + 0.3*r + 0.1*0


# ----------------------------------------------------------------------------
# pick_top_speaker
# ----------------------------------------------------------------------------


def test_pick_top_speaker_returns_none_below_threshold() -> None:
    """All scores < threshold → None.  Use weights=0 so every score is 0.0."""
    weights = TriggerWeights(
        uncertainty=0.0,
        condition_reinforcement=0.0,
        cost=0.0,
        topic_match=0.0,
        threshold=0.5,
    )
    scorer = TriggerScorer(weights=weights)
    msg = _msg("hi", speaker_key="user")
    scores = scorer.score_message(msg, ["shu-hang"], [], PERSONA_KEYWORDS)
    assert scores[0].score == 0.0
    assert scorer.pick_top_speaker(scores) is None


def test_pick_top_speaker_returns_top_above_threshold() -> None:
    """2 NPCs above threshold → returns the higher one (shu-hang via @-mention)."""
    weights = TriggerWeights(threshold=0.0)  # any non-negative score wins
    scorer = TriggerScorer(weights=weights)
    msg = _msg("@shu-hang something", speaker_key="user")
    scores = scorer.score_message(
        msg, ["yao-shi", "shu-hang"], [], PERSONA_KEYWORDS,
    )
    assert scores[0].role_key == "shu-hang"  # confirm ordering
    assert scorer.pick_top_speaker(scores) == "shu-hang"


# ----------------------------------------------------------------------------
# hourly cap
# ----------------------------------------------------------------------------


def test_hourly_cap_blocks_top_speaker() -> None:
    """After `hourly_cap` `record_spoke` calls, `can_speak` is False and
    `pick_top_speaker` returns None even though the top score is high.
    """
    weights = TriggerWeights(hourly_cap=5, threshold=0.0)
    scorer = TriggerScorer(weights=weights)
    for _ in range(5):
        scorer.record_spoke("shu-hang")
    # Sanity: shu-hang is now blocked
    assert scorer.can_speak("shu-hang") is False
    # Other roles are still allowed
    assert scorer.can_speak("yao-shi") is True

    msg = _msg("@shu-hang hello there", speaker_key="user")
    scores = scorer.score_message(msg, ["shu-hang", "yao-shi"], [], PERSONA_KEYWORDS)
    # shu-hang still has the highest score (the cap is enforced by
    # pick_top_speaker, not by score_message).
    assert scores[0].role_key == "shu-hang"
    # But pick_top_speaker returns None because the top is blocked.
    assert scorer.pick_top_speaker(scores) is None


def test_record_spoke_trims_after_1h() -> None:
    """`record_spoke` followed by `can_speak` after time-travel past 1h
    should re-allow the role.  We simulate the time travel by injecting
    synthetic old timestamps into the internal log, then calling
    `can_speak` which trims and reports the new count.
    """
    import time as _time

    weights = TriggerWeights(hourly_cap=2, threshold=0.0)
    scorer = TriggerScorer(weights=weights)
    # Inject 2 timestamps that are > 1h old.  can_speak should trim and
    # then report True.
    now = _time.time()
    scorer._hourly_log["bei-he"] = [now - 7200.0, now - 3601.0]  # 2h and ~1h+1s ago
    assert scorer.can_speak("bei-he") is True


# ----------------------------------------------------------------------------
# topic_match (t)
# ----------------------------------------------------------------------------


def test_topic_match_uses_persona_keywords() -> None:
    """Msg tokens that overlap with persona_keywords → t > 0 for that role."""
    scorer = TriggerScorer()
    msg = _msg("丹道 药方", speaker_key="user")
    # yao-shi's keywords include 药/丹/方 → overlap with msg tokens
    scores = scorer.score_message(msg, ["yao-shi", "san-lang"], [], PERSONA_KEYWORDS)
    yao = next(s for s in scores if s.role_key == "yao-shi")
    san = next(s for s in scores if s.role_key == "san-lang")
    assert yao.breakdown["t"] > 0
    # san-lang's keywords (刀/浪/赤血/三浪/爽快/六品) have zero overlap
    # with {丹, 道, 药, 方}.
    assert san.breakdown["t"] == 0.0


# ----------------------------------------------------------------------------
# persona keyword derivation
# ----------------------------------------------------------------------------


def test_derive_persona_keywords_filters_stopwords() -> None:
    """The derive function drops common stopwords (你, 是, 的, the, ...) and
    splits CJK into individual chars, so a downstream caller gets a clean
    keyword set usable for topic_match intersection.
    """
    system = "你是【宋书航】——九洲一号群的主角。性格：自嘲。境界：灵尊。说话风格：妈耶。\n"
    kws = derive_persona_keywords(system)
    # Each CJK char of the name is a keyword
    assert "宋" in kws
    assert "书" in kws
    assert "航" in kws
    # Function words are NOT in the keyword set
    assert "你" not in kws
    assert "是" not in kws
    assert "的" not in kws
    # The name `九洲一号群` tokenizes into 5 individual chars
    for ch in "九洲一号群":
        assert ch in kws, f"missing {ch!r} in derived keywords"
    # Persona-marker phrases stay (e.g. `妈耶` is CJK; with 妈 + 耶 individually)
    assert "妈" in kws
    assert "耶" in kws
