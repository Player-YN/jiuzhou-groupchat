"""Per-message trigger scoring for 九洲一号群 NPC self-driven selection.

P0-B of the self-driven trigger plan: when a new group message arrives, score all
6 九洲一号群 NPCs against the message using a deterministic, 4-feature weighted
heuristic.  The top scorer (if above threshold AND not in cooldown) is the chosen
speaker for that message — replacing the fixed 5-minute cron with per-message
signal-driven selection.

This module is **100% deterministic** — no LLM calls, no Letta, no DB.  It is a
pure function: same inputs → same outputs.  This makes it cheap to call inline
in the WS handler / `make_agent_node` path AND trivial to unit-test in isolation.

The 4 features (all in [0, 1] range; `cost` is SUBTRACTED from the score):

    u  uncertainty            — How "fresh" is this topic for the NPC?  Binary:
                                  1.0 if the new message has no token overlap
                                  with the NPC's own recent utterances, else
                                  0.0.  Empty NPC history also yields 1.0.
    r  condition_reinforcement — Was this NPC mentioned by name (substring,
                                  catches "@-mentions" like "@shu-hang" and
                                  plain text mentions), OR by a persona keyword?
    c  cost                   — Has this NPC already spoken in the last 2 min?
                                  Or have ≥ 3 distinct NPCs already replied to
                                  the same message window?  Subtracted from
                                  score — high cost → less likely to speak again.
    t  topic_match            — How well does the message content match the
                                  NPC's persona keywords?

Final score:    score = w_u*u + w_r*r - w_c*c + w_t*t
Top-1 pick:     if score >= threshold AND can_speak(role_key): return role_key

Hourly cap: each role can speak at most `hourly_cap` times per rolling 1h window
(via `_hourly_log[role_key]` list of `time.time()` epoch seconds).

Tokenization (shared by `score_message` and `derive_persona_keywords`):
- CJK characters → one-char tokens (e.g. "宋书航" → {"宋", "书", "航"}).
- Non-CJK runs (letters / digits / punctuation) collapse into one lowercase
  word token (e.g. "@shu-hang" → "@shu-hang").
- Whitespace and CJK punctuation are delimiters.
- Result is a `set[str]` for O(1) intersection.

Public API:
    `TriggerWeights`         — 4 weight floats + threshold + hourly_cap
    `TriggerScore`           — one NPC's score + 4-feature breakdown
    `TriggerScorer`          — the engine; one instance per scheduler loop
    `derive_persona_keywords` — utility: turn a system-prompt string into a
                                persona_keywords list
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Iterable


# ============================================================================
# Public dataclasses
# ============================================================================


@dataclass
class TriggerWeights:
    """4-feature weights + threshold + hourly cap.

    All weight floats should be in [0, 1]; the 4 weights don't need to sum
    to 1 (the formula is independent of that).  `threshold` is the minimum
    final score required for a top speaker to be selected; `hourly_cap`
    is the rolling-1h ceiling on how many times one role can be selected
    as top speaker (per `TriggerScorer` instance).
    """

    uncertainty: float = 0.4
    condition_reinforcement: float = 0.3
    cost: float = 0.2
    topic_match: float = 0.1
    threshold: float = 0.5
    hourly_cap: int = 5


@dataclass
class TriggerScore:
    """One NPC's per-message score + 4-feature breakdown.

    `breakdown` is a `dict` with string keys "u", "r", "c", "t" and float
    values in [0, 1].  Useful for logging and for test assertions.
    """

    role_key: str
    score: float
    breakdown: dict = field(default_factory=dict)


# ============================================================================
# TriggerScorer
# ============================================================================


class TriggerScorer:
    """Deterministic 4-feature trigger scorer for group messages.

    NOT thread-safe across threads (the internal `_hourly_log` dict has no
    lock).  The intended pattern is one instance per scheduler loop / per
    WS handler; the hourly cap is local to the instance, not a global
    cross-process quota.

    The `score_message` method is pure given its inputs; `record_spoke` is
    the only stateful operation.  `pick_top_speaker` reads the current
    hourly cap state but does not mutate it (the caller is expected to
    call `record_spoke` after a successful speaker selection).
    """

    # 2 minutes, expressed in ms (matches AgentMemoryEntry.timestamp unit).
    COST_WINDOW_MS: int = 120_000
    # 1 hour, expressed in seconds (matches `time.time()` unit).
    HOUR_WINDOW_S: float = 3600.0

    def __init__(self, *, weights: TriggerWeights | None = None) -> None:
        self.weights: TriggerWeights = weights or TriggerWeights()
        # role_key -> list[float] of `time.time()` epoch seconds when this
        # role was selected as top speaker.  Trimmed on every read/write.
        self._hourly_log: dict[str, list[float]] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def score_message(
        self,
        msg: dict,
        role_keys: Iterable[str],
        recent_events: list,
        persona_keywords: dict,
    ) -> list[TriggerScore]:
        """Score one group message against all NPCs.

        Args:
            msg: ``{"text": str, "speaker_key": str, "ts": float}`` dict.
                - ``text``: message text
                - ``speaker_key``: ``"user"`` or one of the 6 role keys
                  (the actual speaker; used by `cost` to know "did this
                  NPC already speak recently")
                - ``ts``: epoch **milliseconds** (matches
                  `AgentMemoryEntry.timestamp` unit so the 2-minute
                  cost window is a simple `> msg_ts - 120_000`).
            role_keys: iterable of all NPC role keys to score (typically
                the canonical 6-tuple `ROLE_AGENT_KEYS`).
            recent_events: list of recent group events
                (`AgentMemoryEntry`-like — duck-typed via `getattr`).
                Each entry must expose `speaker_key`, `text`, `timestamp`
                and (for cost) `role`.  May be empty.
            persona_keywords: ``{role_key: list[str]}`` — keyword set for
                each NPC's persona.  Used by `condition_reinforcement` and
                `topic_match`.  Missing role → empty list (treated as no
                signal).

        Returns:
            list of `TriggerScore`, sorted by `score` descending (stable
            on equal scores via `list.sort`).
        """
        msg_text: str = msg.get("text", "") or ""
        msg_ts_ms: float = float(msg.get("ts", 0.0))
        msg_tokens: set[str] = _tokenize(msg_text)

        scores: list[TriggerScore] = []
        for rk in role_keys:
            u = _feature_uncertainty(msg_tokens, recent_events, rk)
            r = _feature_condition_reinforcement(msg_text, msg_tokens, rk, persona_keywords)
            c = _feature_cost(recent_events, rk, msg_ts_ms)
            t = _feature_topic_match(msg_tokens, rk, persona_keywords)
            w = self.weights
            score = (
                w.uncertainty * u
                + w.condition_reinforcement * r
                - w.cost * c
                + w.topic_match * t
            )
            scores.append(
                TriggerScore(
                    role_key=rk,
                    score=score,
                    breakdown={"u": u, "r": r, "c": c, "t": t},
                )
            )

        # Descending; equal-score stable on input order (Python's sort is
        # stable, so `list.sort(key=...)` keeps input order on ties).
        scores.sort(key=lambda s: s.score, reverse=True)
        return scores

    def pick_top_speaker(self, scores: list[TriggerScore]) -> str | None:
        """Return the top-1 `role_key` iff its score >= threshold AND
        `can_speak(role_key)` is True.

        Lower-scoring roles are not consulted.  If the top-1 fails the
        gate (threshold or hourly cap), the function returns None — we
        do NOT fall back to a lower-scored role here, because doing so
        would silently override the scoring intent.  The caller is
        responsible for any fallback strategy (e.g. "no one speaks this
        round" or "use a random NPC as tie-breaker").
        """
        if not scores:
            return None
        top = scores[0]
        if top.score < self.weights.threshold:
            return None
        if not self.can_speak(top.role_key):
            return None
        return top.role_key

    def can_speak(self, role_key: str) -> bool:
        """Return True if `role_key` has fewer than `hourly_cap` entries
        in the rolling 1h window of `_hourly_log`.

        Trims stale entries (older than 1h) as a side-effect.
        """
        now = time.time()
        self._trim_old(role_key, now)
        log = self._hourly_log.get(role_key, [])
        return len(log) < self.weights.hourly_cap

    def record_spoke(self, role_key: str) -> None:
        """Append `time.time()` to `_hourly_log[role_key]` and trim
        entries older than 1h.
        """
        now = time.time()
        self._trim_old(role_key, now)
        self._hourly_log.setdefault(role_key, []).append(now)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _trim_old(self, role_key: str, now: float) -> None:
        """Drop entries older than `HOUR_WINDOW_S` from `_hourly_log[role_key]`."""
        log = self._hourly_log.get(role_key)
        if not log:
            return
        cutoff = now - self.HOUR_WINDOW_S
        # log is append-only; old entries are at the front.  We use a
        # list comprehension rather than in-place mutation for clarity.
        # In practice the list is at most `hourly_cap` items long, so the
        # O(n) scan is fine.
        keep = [t for t in log if t >= cutoff]
        if keep:
            self._hourly_log[role_key] = keep
        else:
            # Avoid leaving an empty list around — reduces memory churn
            # for roles that haven't spoken in over an hour.
            self._hourly_log.pop(role_key, None)


# ============================================================================
# Feature extractors (module-level, pure)
# ============================================================================


def _is_cjk(ch: str) -> bool:
    """Return True for CJK Unified Ideographs and common CJK / fullwidth blocks.

    Covers the practical 90% of Chinese text without pulling in `unicodedata`:
      - U+4E00..U+9FFF      CJK Unified Ideographs
      - U+3400..U+4DBF      CJK Unified Ideographs Extension A
      - U+3000..U+303F      CJK Symbols and Punctuation (delimiters — see
                             `_is_cjk_punct` for the actual punctuation chars)
      - U+FF00..U+FFEF      Halfwidth and Fullwidth Forms
    """
    cp = ord(ch)
    return (
        0x4E00 <= cp <= 0x9FFF
        or 0x3400 <= cp <= 0x4DBF
        or 0x3000 <= cp <= 0x303F
        or 0xFF00 <= cp <= 0xFFEF
    )


# A small set of CJK punctuation chars we treat as token delimiters (i.e. not
# kept as standalone tokens).  CJK whitespace and full-width space are
# handled by `str.isspace()` already, so we only need to list the visible
# punctuation marks here.
_CJK_PUNCT: frozenset[str] = frozenset("，。、！？：；…—「」『』《》（）")


def _is_cjk_punct(ch: str) -> bool:
    """Return True for CJK punctuation used as token delimiters."""
    return ch in _CJK_PUNCT


def _tokenize(text: str) -> set[str]:
    """Tokenize text into a set of tokens.

    Rules (per spec):
      - CJK characters → one-char tokens each.
      - Non-CJK runs (letters / digits / punctuation) collapse into one
        lowercase word token.
      - Whitespace and CJK punctuation are delimiters (not tokens).

    Returns a `set` for O(1) intersection in feature extractors.
    """
    if not text:
        return set()
    tokens: set[str] = set()
    cur: list[str] = []
    for ch in text:
        if _is_cjk(ch):
            if cur:
                tokens.add("".join(cur).lower())
                cur = []
            if not _is_cjk_punct(ch):
                tokens.add(ch)
        elif ch.isspace():
            if cur:
                tokens.add("".join(cur).lower())
                cur = []
        else:
            cur.append(ch)
    if cur:
        tokens.add("".join(cur).lower())
    return tokens


def _feature_uncertainty(
    msg_tokens: set[str],
    recent_events: list,
    role_key: str,
) -> float:
    """u = 1.0 if `msg_tokens` ∩ `own_tokens` is empty, else 0.0.

    `own_tokens` is the union of tokens across all recent events whose
    `speaker_key == role_key` (the NPC's own recent utterances).  If the
    NPC has not spoken recently, `own_tokens` is empty and u = 1.0.

    We use the binary 0.0 / 1.0 form (rather than the continuous
    ``1 - |overlap| / |msg_tokens|``) for two reasons:
      1. Tests demand it (`test_uncertainty_zero_when_overlap`).
      2. The signal is "should this NPC add value to the conversation?"
         — any overlap means they have already contributed on the same
         tokens, so the value-add is binary: yes or no.
    """
    own_tokens: set[str] = set()
    for ev in recent_events:
        if getattr(ev, "speaker_key", None) != role_key:
            continue
        ev_text = getattr(ev, "text", "") or ""
        own_tokens |= _tokenize(ev_text)
    if not own_tokens or not msg_tokens:
        return 1.0
    overlap = msg_tokens & own_tokens
    return 0.0 if overlap else 1.0


def _feature_condition_reinforcement(
    msg_text: str,
    msg_tokens: set[str],
    role_key: str,
    persona_keywords: dict,
) -> float:
    """r = 1.0 if `role_key` is a substring of `msg_text` (catches
    `@-mentions` like `@shu-hang` and plain text mentions like
    "ask shu-hang to come"), OR if any token in
    `persona_keywords[role_key]` is in `msg_tokens`.

    Otherwise r = 0.0.
    """
    if not msg_text:
        return 0.0
    # Substring match — catches "@shu-hang", "shu-hang", etc.  This is
    # the primary signal: a direct @-mention should always trigger r=1.0
    # for the mentioned role, regardless of which persona-keyword list
    # was passed in.
    if role_key in msg_text:
        return 1.0
    kws = persona_keywords.get(role_key) or []
    if not kws:
        return 0.0
    # Persona-keyword token match — any kw appearing as a token in msg
    # (set membership) flips the signal on.  We use `in msg_tokens`
    # rather than `in msg_text` to avoid the substring-vs-token ambiguity
    # (e.g. persona kw "药" should not match msg "要药" substring just
    # because "药" is present).
    for kw in kws:
        if not isinstance(kw, str) or not kw:
            continue
        if kw in msg_tokens:
            return 1.0
    return 0.0


def _feature_cost(
    recent_events: list,
    role_key: str,
    msg_ts_ms: float,
) -> float:
    """c ∈ {0.0, 0.5, 1.0} (binary-or-half cost).

    Two signals:
      1. Direct self-cost: if `role_key` spoke in the last 2 min
         (msg_ts_ms - 120_000), c = 1.0.
      2. Crowd-cost: if ≥ 3 distinct NPC keys have already replied as
         `role == 'agent'` in the same window, c is bumped to max(c, 0.5).

    The crowd-cost is half-strength so a NPC that just spoke is still
    more expensive than one that only faces a noisy room.
    """
    window_start = msg_ts_ms - TriggerScorer.COST_WINDOW_MS
    spoke_recently = False
    recent_replier_keys: set[str] = set()
    for ev in recent_events:
        ev_ts = getattr(ev, "timestamp", 0)
        if ev_ts is None or ev_ts < window_start:
            continue
        ev_speaker = getattr(ev, "speaker_key", None)
        if ev_speaker == role_key:
            spoke_recently = True
        # Count distinct NPCs (agent speakers) that replied recently.  We
        # filter on role=='agent' so a recent user message doesn't count
        # toward the NPC reply tally.
        if getattr(ev, "role", None) == "agent" and ev_speaker:
            recent_replier_keys.add(ev_speaker)

    c = 1.0 if spoke_recently else 0.0
    if len(recent_replier_keys) >= 3:
        c = max(c, 0.5)
    return c


def _feature_topic_match(
    msg_tokens: set[str],
    role_key: str,
    persona_keywords: dict,
) -> float:
    """t = |msg_tokens ∩ persona_keywords[role_key]| / max(1, |kw_set|),
    clipped to [0, 1].

    We dedupe persona_keywords to a set before measuring the denominator
    so a caller passing a list with duplicates doesn't accidentally
    inflate the result.  Missing role → empty list → 0.0.
    """
    kws = persona_keywords.get(role_key) or []
    if not kws:
        return 0.0
    kw_set = {kw for kw in kws if isinstance(kw, str) and kw}
    if not kw_set:
        return 0.0
    overlap = msg_tokens & kw_set
    t = len(overlap) / max(1, len(kw_set))
    if t < 0.0:
        return 0.0
    if t > 1.0:
        return 1.0
    return t


# ============================================================================
# Persona keyword extraction (utility)
# ============================================================================


# Common stopwords (Chinese + English) — filtered out of `derive_persona_keywords`
# output so the topic_match signal isn't drowned in noise like "的 / 是 / the".
_STOPWORDS: frozenset[str] = frozenset({
    # Chinese function words
    "的", "了", "是", "在", "和", "有", "也", "就", "要", "不",
    "我", "你", "他", "她", "它", "们", "这", "那", "么", "之", "于",
    "上", "下", "中", "对", "以", "来", "去", "可", "能", "为", "所",
    "但", "而", "或", "如", "若", "虽", "然", "后", "前", "时", "间",
    "让", "把", "被", "从", "向", "到", "用", "因", "所以", "因为",
    "吗", "呢", "啊", "吧", "哦", "嗯", "哈", "呀", "啦", "嘛",
    # English function words
    "the", "a", "an", "and", "or", "but", "in", "on", "at", "to",
    "is", "are", "was", "were", "be", "been", "being", "have", "has",
    "had", "do", "does", "did", "of", "for", "with", "as", "by", "this",
    "that", "it", "its", "i", "you", "he", "she", "we", "they",
    "me", "him", "her", "us", "them", "my", "your", "his", "our",
    "their", "if", "then", "else", "when", "where", "while", "from",
})


def derive_persona_keywords(system_prompt: str) -> list[str]:
    """Derive persona keywords from a role's `system` prompt string.

    Algorithm:
      1. Tokenize using the same CJK char-level + whitespace split as
         `_tokenize` (so the resulting keywords can be `&`-intersected
         with `msg_tokens` in `_feature_topic_match`).
      2. Drop stopwords (`_STOPWORDS`) and pure CJK punctuation chars.
      3. Sort for deterministic output (handy in tests + log diffs).

    Args:
        system_prompt: the role's `system` field (e.g.
            `app.graph.ROLES[role_key]["system"]`).

    Returns:
        Sorted list of unique keyword strings.  May be empty for very
        short / very stopword-heavy prompts.

    Example:
        >>> kws = derive_persona_keywords("你是【宋书航】——九洲一号群的主角")
        >>> "宋" in kws and "书" in kws and "航" in kws
        True
    """
    if not system_prompt:
        return []
    raw = _tokenize(system_prompt)
    return sorted(
        tok
        for tok in raw
        if tok and tok not in _STOPWORDS and not _is_cjk_punct(tok)
    )


__all__ = [
    "TriggerWeights",
    "TriggerScore",
    "TriggerScorer",
    "derive_persona_keywords",
]
