# ADR-0007: Group Chat NPC Self-Driven "I'd like to chime in" Architecture

**Status**: ACCEPTED (Option B: per-NPC autonomous loop, user confirmed 2026-07-07 03:46)
**Date**: 2026-07-07
**Deciders**: Planner (Mavis), User
**Supersedes**: none
**Related**: ADR pending (group-chat context model + Letta rate-limit constraints)
**Note on naming**: this repo previously used a Chinese-only codename for the project. As of 2026-07-07 the project is referred to in this document by its English product name "Group Chat" / "Jiuzhou No.1 Group" (九洲一号群). Internal code identifiers (e.g. `XiuzhenCronService`, env vars `XZ_*`) are intentionally kept unchanged; see ADR-0006 for the rename policy.

---

## Y-Statement

> **Because** the current `XiuzhenCronService` mechanically picks a random NPC every 5 minutes and pushes one canned line ("what do you want to say to the group"), and the user reported it "doesn't feel like a real person", **we need to** pick a self-driven NPC architecture where each NPC has its own judgment of "do I want to say something or stay quiet", **so that** the group chat looks like 6 humans chatting instead of a cron polling loop.

---

## Context

### Current state (Stage 8 cron commit `7ae1680`)

`backend/app/scheduler/xiuzhen_cron.py`:
- `AsyncIOScheduler` + `IntervalTrigger(5 min)` strictly time-triggered
- Each fire: randomly pick 1 of 6 NPCs → `_stream_via_letta(role_key=npc, session_id="cron-{npc}", all_msgs=[system_persona, human("[system] what do you want to say to the group?")])` → stream → fan-out to all 6 NPC memories → push `cron_agent_post` WS event
- Same-NPC throttle 1h (prevent one NPC from spamming)

`backend/app/scheduler/dm_followup.py`:
- 1h interval scan `AgentMemoryStore` for `(session_id, agent_key)` pairs idle > 24h
- Call `_stream_via_letta` to let NPC proactively DM

### User feedback (2026-07-07)

> "Who is scheduling who speaks? I want to change this to NPC self-driven, like a real person having the 'I'd like to chime in' feeling"

The group chat looks "mechanical" today because:
1. **Trigger cadence is fixed** (every 5 min), not context-driven
2. **Speaker selection is random**, not based on "who just spoke / who was @-ed / whose persona matches the topic"
3. **Prompt template is hard-coded** (`[system] what do you want to say to the group?`), with no awareness of "X just said Y, I want to follow up"
4. **Throttle is time-only**, not considering "this NPC has spoken 3 times already, others haven't had a chance"

### Group-chat constraints

- **Letta v0.16.8 + openai-proxy/MiniMax-M2.7-highspeed** (already committed in `a1c7499`)
- 6 NPC roles, each with its own dedicated Letta agent (`npc-shu-hang` etc.), each with memory.blocks=4 + persona
- AgentMemoryStore SQLite is persistent (Stage 6 DM Phase 2)
- Group-chat context volume: empirically ~50-80 messages per hour of group chat
- minimax M2.7-highspeed has implicit **rate limits** (no docs on requests-per-minute); high LLM call volume can hit 429

---

## Options

### Option A — Enhanced scheduler (NPC mood / urge model)

**Idea**: keep the central scheduler but add an `urge_score` per NPC; scheduler polls and picks the NPC with the highest urge.

```python
class NpcUrge:
    role_key: str
    base_urge: float          # group-chat average expressiveness (per persona)
    last_spoke_at: float
    last_mentioned_at: float  # last time @-ed
    recent_topic_affinity: float  # how well current topic fits this NPC's persona
    # ...

def compute_urge(npc: NpcUrge, ctx: GroupContext) -> float:
    time_since_spoke = now() - npc.last_spoke_at
    silence_score = sigmoid(time_since_spoke / 1800)   # 30-min silence weight
    mention_score = exp(-(now() - npc.last_mentioned_at) / 600)
    affinity = cosine_sim(ctx.current_topic_embedding, npc.persona_embedding)
    return silence_score * 0.4 + mention_score * 0.4 + affinity * 0.2
```

**Trigger**: scheduler polls every 30s (10x more frequent than current 5min), but **only actually pushes** when `urge > threshold`.

**Pros**:
- Centralized control, only 1 NPC streaming at a time (natural semaphore)
- Feels less mechanical than current (++) because cadence and selection are now context-driven
- Reuses AgentMemoryStore group timeline, no new storage needed
- Fast response to group-chat context changes

**Cons**:
- Urge model is heuristic, not "real LLM decision"
- Embedding computation needs a new dep (sentence-transformers or OpenAI embedding)
- Cold start (fresh group) all NPCs have equal urge, behaves like current

**Estimate**: 1.5 person-days (urge model + scheduler rework + tests)

---

### Option B — Per-NPC autonomous agentic loop

**Idea**: each NPC runs its own `asyncio.create_task(_npc_loop(npc))`. Loop rhythm: think (read recent group context) → LLM decide whether to speak → if yes, stream → if no, sleep (random 30s-3min).

```python
async def _npc_loop(role_key: str):
    while True:
        try:
            recent = agent_memory.load_recent_group_events(limit=20)
            decision_prompt = f"""You are {ROLES[role_key]['display_name']}.
The group chat just had: {recent_text}
Do you want to chime in? If yes, briefly reply with 1 sentence; if no, reply with <silent/>."""
            resp = await _stream_via_letta(role_key=role_key, all_msgs=[SystemMessage(persona), HumanMessage(decision_prompt)])
            full = await resp.collect()
            if "<silent/>" not in full:
                await _push_to_group(role_key, full)
                await asyncio.sleep(random.uniform(60, 300))
            else:
                await asyncio.sleep(random.uniform(30, 120))
        except Exception:
            await asyncio.sleep(60)
```

**Concurrency control**: all 6 loops must pass `asyncio.Semaphore(1)` before pushing + enforce "no second NPC speaks within 10s of last push".

**Pros**:
- Feels most like a real person (+++) — each NPC decides for itself
- Group-chat behavior is distributed, no obvious "every 5 min we refresh" pattern

**Cons**:
- **6 loops × 1-3 calls/min = 6-18 LLM calls/min** even when most replies are `<silent/>` (each `<silent/>` still costs one Letta call)
- minimax M2.7-highspeed rate-limit likely to be hit (we have seen 429 in production)
- Concurrency needs semaphore + last_spoke global lock, more complex than A
- 6 loops' startup ordering + exception recovery all need handling

**Estimate**: 3 person-days (loop + semaphore + Letta rate-limit adaptation + tests + retry)

---

### Option C — Event-driven + LLM decision

**Idea**: group-chat context flow (new messages, @ mentions, idle) acts as "stimulus" trigger; a central LLM "selector" decides whether to chime in + which NPC should chime in.

```
each new message enters group chat → push to event_queue
selector_loop:
    if event_queue not empty:
        events = drain(event_queue)
        prompt = """The group chat just had: {events}
Should anyone chime in now? Pick 1 NPC who most wants to, or <silent/>"""
        decision = await llm_call(prompt)
        if "<silent/>" in decision:
            return
        role_key = parse_choice(decision)
        await _stream_via_letta(role_key=role_key, ...)
```

**Pros**:
- Feels most like a real person (++++) — selector judges context itself
- Event-driven, no wasted polls during idle
- Selector choice is explainable ("Song Shuhang just mocked, Bai-qianbei would naturally follow up")

**Cons**:
- **Every message triggers 1 LLM call** (selector decision); high-frequency group chat blows through Letta rate limit
- Selector prompt design is hard — 6 NPC personality differences require precise selector prompt
- Idle group chat (no messages) still needs extra timer + idle detection to drive proactive speech
- Hard to debug (LLM decision black box)

**Estimate**: 4 person-days (event queue + selector prompt + idle detection + tests + Letta rate-limit handling)

---

## Comparison

| Dimension | A (urge model) | B (autonomous loop) | C (event + LLM) |
|------|---------------|---------------|----------------|
| Realism | ++ | +++ | ++++ |
| LLM calls (group-chat per hour) | ~12 (same as current) | ~120-360 | ~80-200 |
| Letta rate-limit risk | Low | **High** | **High** |
| Implementation complexity | Medium | High | High |
| Estimate | 1.5 person-days | 3 person-days | 4 person-days |
| Explainability | urge values readable | LLM prompt debug | selector black box |
| Exception recovery | scheduler built-in | 6 loops each need care | selector single point |

---

## Decision

**Chose B: each NPC runs an autonomous agentic loop.**

Reasons (user confirmed 2026-07-07 03:46):

1. **The user prioritizes "realism"** over **5-15x LLM call cost**. This is a value judgment, plan/verifier should not override.
2. **Letta rate-limit risk is manageable**: group-chat semaphore (only 1 NPC streaming at a time) + per-NPC 1-3 min randomized sleep + minimax 429 retry-with-jitter. Code-level controllable.
3. **Per-NPC loop exception recovery**: each loop has its own try/except; a dead loop can be detected and restarted independently.

### Option B scope

1. **New** `backend/app/scheduler/npc_loop.py`:
   - `NpcLoop` dataclass (role_key, last_spoke_at, semaphore)
   - `_npc_loop(role_key)` async coroutine
   - `start_all()` / `stop_all()` lifecycle in `app.scheduler.lifespan`
   - Each loop reads recent group events from `AgentMemoryStore` (limit 20)
   - Calls `_stream_via_letta(role_key=..., all_msgs=[SystemMessage(persona), HumanMessage(decision_prompt)])`
   - Parses `<silent/>` token; if found, sleeps 30-120s; if absent, pushes to group + sleeps 60-300s

2. **Concurrency primitives** `backend/app/scheduler/group_semaphore.py`:
   - `GroupChatSemaphore` — `asyncio.Semaphore(1)` + `last_push_at` lock
   - 6 loops compete for permission before pushing
   - Rejects pushes within 10s of last push (cool-down)

3. **Rate-limit handling** `backend/app/scheduler/letta_retry.py`:
   - Wraps `_stream_via_letta` with retry-on-429 + exponential backoff + jitter
   - After 3 failed retries, loop sleeps 5 min before trying again
   - Avoids one bad NPC starving others

4. **Decision prompt template**:
   - Replace the canned `[system] what do you want to say to the group?` with a context-aware prompt that includes the recent 20 group events + NPC's persona + a `<silent/>` instruction
   - Prompt designed to encourage short replies (1-2 sentences) to keep call volume down

5. **Tests** `backend/tests/test_npc_loop.py`:
   - `test_loop_speaks_when_decision_not_silent`
   - `test_loop_stays_silent_when_decision_silent`
   - `test_loop_sleeps_after_push`
   - `test_loop_handles_letta_429_with_retry`
   - `test_loop_respects_group_semaphore`
   - `test_loop_revives_after_exception`
   - `test_6_loops_dont_all_fire_simultaneously`

6. **Do NOT change**:
   - `dm_followup.py` (DM proactive messaging is a separate mechanism)
   - `ConnectionRegistry` (push channel unchanged)
   - `cron_agent_post` WS event type (frontend still listens to this)

### Acceptance criteria (verifier PASS must)

1. ✅ pytest all old (45) + new (≥7) tests pass
2. ✅ ruff clean on new files
3. ✅ live `curl /api/cron/status` returns `running: true` for both group loop and DM followup
4. ✅ live `/api/cron/toggle` can pause / resume the loop pool
5. ✅ live fire a single loop manually → see one NPC message in group chat (use mock cron trigger)
6. ✅ live 6 loops running 1 min: no two NPCs speak within 10s of each other (semaphore enforced)
7. ✅ live force Letta 429 → loop retries 3x with backoff + then sleeps 5min (no crash)

### Out of scope / Risks

- **No embedding / topic-affinity**: NPC personality match with topic is decided by LLM itself, not by pre-computed embeddings
- **No conversation persistence across loop restarts**: in-process state only; BFF restart = loops restart from scratch (acceptable for now, accept risk)
- **No "user-typing-aware" suppression**: if user is actively typing, NPC may still chime in; future sprint can add active-typing detection in `ConnectionRegistry`
- **No selector for "who responds to @-mention"**: if user @-s NPC X, that NPC may or may not be the one to respond; depends on LLM persona. Accept for v1, refine in v2

---

## Next step

This ADR is the spec for T5 in the team plan. Coder reads this ADR + AGENTS.md Stage 8 section, then implements per the scope above.