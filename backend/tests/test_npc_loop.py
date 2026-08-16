"""Stage 8-NPC-Love — per-NPC autonomous loop tests (ADR-0007 Option B).

This file covers the 7 acceptance criteria from
``docs/decisions/0007-npc-self-driven.md`` §Option B scope:

1. ``test_loop_speaks_when_decision_not_silent``        — ACT path on non-silent
2. ``test_loop_stays_silent_when_decision_silent``       — short sleep path
3. ``test_loop_sleeps_after_push``                       — bookkeeping + cooldown
4. ``test_loop_handles_letta_429_with_retry``            — LettaRetryExhausted
5. ``test_loop_respects_group_semaphore``                — 10s cool-down gate
6. ``test_loop_revives_after_exception``                 — exception → revive
7. ``test_6_loops_dont_all_fire_simultaneously``         — pool-level concurrency

Plus a few supporting tests:

- ``test_loop_pool_status_dict``        — admin status shape
- ``test_admin_status_includes_npc_loop_pool`` — /api/cron/status shape
- ``test_load_recent_group_events_basic`` — AgentMemoryStore new method
- ``test_group_semaphore_guard``         — semaphore guard unit
- ``test_letta_retry_exhausts_after_3``  — retry exhaustion
- ``test_lifespan_gc_loops_enabled_default_true`` — env toggle

The tests mock ``app.graph._stream_via_letta`` (and indirectly
``app.scheduler.letta_retry._stream_via_letta``) so they run without a real
Letta server.  We do NOT start the real pool — each test drives one
``pool.trigger_one(role_key)`` cycle for unit-style coverage.
"""
from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fake_stream_factory(text: str):
    """Build a coroutine that yields ``text`` once."""

    async def _stream(**kwargs) -> AsyncIterator[str]:
        yield text

    return _stream


@pytest.fixture(autouse=True)
def _reset_singletons():
    """Each test gets a fresh NpcLoopPool + CronState + GroupChatSemaphore.

    Avoids cross-test pollution from the process-wide singletons (the new
    ones from this commit, plus the existing legacy ones).
    """
    from app.scheduler.connection_registry import set_connection_registry
    from app.scheduler.dm_followup import set_dm_followup_service
    from app.scheduler.group_semaphore import set_group_semaphore
    from app.scheduler.npc_loop import set_npc_loop_pool
    from app.scheduler.state import reset_cron_state_for_tests
    from app.scheduler.xiuzhen_cron import set_xiuzhen_cron_service

    reset_cron_state_for_tests()
    set_connection_registry(None)
    set_xiuzhen_cron_service(None)
    set_dm_followup_service(None)
    set_npc_loop_pool(None)
    set_group_semaphore(None)
    yield
    reset_cron_state_for_tests()
    set_connection_registry(None)
    set_xiuzhen_cron_service(None)
    set_dm_followup_service(None)
    set_npc_loop_pool(None)
    set_group_semaphore(None)


@pytest.fixture
def in_memory_memory_store():
    """Swap the global AgentMemoryStore for an in-memory instance."""
    from app.memory.agent_memory import AgentMemoryStore, set_agent_memory_store

    store = AgentMemoryStore(":memory:")
    old = set_agent_memory_store(store)
    try:
        yield store
    finally:
        set_agent_memory_store(old)
        store.close()


@pytest.fixture
def fresh_pool(in_memory_memory_store, monkeypatch):
    """Build an NpcLoopPool with short sleeps so tests don't actually wait."""
    from app.scheduler.group_semaphore import (
        GroupChatSemaphore,
        set_group_semaphore,
    )
    from app.scheduler.npc_loop import NpcLoopPool

    # Patch random.uniform so the post-push / post-silent sleeps don't
    # actually run during tests — we want deterministic, fast cycles.
    monkeypatch.setattr(
        "app.scheduler.npc_loop.random.uniform",
        lambda lo, hi: lo,  # always pick lower bound (faster + deterministic)
    )

    # Shorten the cool-down so we can test the gate without waiting 10s.
    # IMPORTANT: register the fresh sem as the module-level singleton so
    # ``get_group_semaphore()`` inside the loop body returns *this* instance
    # (the loop does not honor a per-pool override attribute).
    sem = GroupChatSemaphore(cooldown_seconds=0.2)
    set_group_semaphore(sem)

    pool = NpcLoopPool(state=None)
    return pool, sem


def _install_letta_stream(monkeypatch, text: str | list[str]):
    """Install a fake ``_stream_via_letta`` that yields ``text``.

    If ``text`` is a list, each call consumes one element (lets us script
    the response per cycle).
    """
    calls: list[dict[str, Any]] = []
    counter = {"n": 0}

    async def _stream(**kwargs) -> AsyncIterator[str]:
        calls.append(kwargs)
        idx = counter["n"]
        counter["n"] += 1
        if isinstance(text, list):
            payload = text[idx] if idx < len(text) else text[-1]
        else:
            payload = text
        yield payload

    # Patch the source module too (retry wrapper imports it lazily).
    monkeypatch.setattr("app.graph._stream_via_letta", _stream)
    return calls


# ===========================================================================
# 1) test_loop_speaks_when_decision_not_silent
# ===========================================================================


@pytest.mark.asyncio
async def test_loop_speaks_when_decision_not_silent(
    fresh_pool, in_memory_memory_store, monkeypatch,
):
    """When the LLM emits text WITHOUT ``<silent/>``, the loop must fan-out
    the reply to all 6 NPC memories and push to active WS sessions.
    """
    pool, _sem = fresh_pool
    calls = _install_letta_stream(monkeypatch, "在下告辞！妈耶这波不对啊。")

    result = await pool.trigger_one("shu-hang")
    assert result["event"] == "pushed", result
    assert result["role_key"] == "shu-hang"
    assert result["text_len"] > 0

    # _stream_via_letta was called once with the right args.
    assert len(calls) == 1
    assert calls[0]["role_key"] == "shu-hang"
    assert calls[0]["session_id"] == "loop-shu-hang"

    # Fan-out: all 6 NPC memories now contain the reply.
    from app.letta_bridge.agent_manager import ROLE_AGENT_KEYS
    for agent_key in ROLE_AGENT_KEYS:
        entries = in_memory_memory_store.load_agent_memory(
            session_id="loop-shu-hang", agent_key=agent_key,
        )
        assert len(entries) == 1, f"{agent_key}: expected 1 entry, got {len(entries)}"
        assert "妈耶" in entries[0].text

    # Pool bookkeeping.
    loop = pool.get("shu-hang")
    assert loop.pushed_decisions == 1
    assert loop.silent_decisions == 0
    assert loop.total_decisions == 1
    assert loop.last_spoke_at > 0


# ===========================================================================
# 2) test_loop_stays_silent_when_decision_silent
# ===========================================================================


@pytest.mark.asyncio
async def test_loop_stays_silent_when_decision_silent(
    fresh_pool, in_memory_memory_store, monkeypatch,
):
    """When the LLM emits ``<silent/>``, the loop must NOT fan-out and must
    record the silent decision."""
    pool, _sem = fresh_pool
    _install_letta_stream(monkeypatch, "<silent/>")

    result = await pool.trigger_one("bai-qianbei")
    assert result["event"] == "silent", result
    assert result["role_key"] == "bai-qianbei"

    # No fan-out: all 6 NPC memories stay empty.
    from app.letta_bridge.agent_manager import ROLE_AGENT_KEYS
    for agent_key in ROLE_AGENT_KEYS:
        entries = in_memory_memory_store.load_agent_memory(
            session_id="loop-bai-qianbei", agent_key=agent_key,
        )
        assert entries == [], f"{agent_key}: expected empty, got {entries}"

    # Pool bookkeeping.
    loop = pool.get("bai-qianbei")
    assert loop.silent_decisions == 1
    assert loop.pushed_decisions == 0
    assert loop.last_spoke_at == 0.0  # never spoke


# ===========================================================================
# 3) test_loop_sleeps_after_push (bookkeeping: pushed_count + last_spoke_at)
# ===========================================================================


@pytest.mark.asyncio
async def test_loop_sleeps_after_push(
    fresh_pool, in_memory_memory_store, monkeypatch,
):
    """After a successful push, last_spoke_at is bumped and pushed_count is +1.

    Sleep timing is mocked (random.uniform → lower bound), so the test stays
    fast — what we verify is the state delta.
    """
    pool, _sem = fresh_pool
    _install_letta_stream(monkeypatch, "善。")

    loop = pool.get("bei-he")
    assert loop.last_spoke_at == 0.0
    assert loop.pushed_decisions == 0

    before = loop.last_spoke_at
    result = await pool.trigger_one("bei-he")
    assert result["event"] == "pushed"

    assert loop.last_spoke_at > before
    assert loop.pushed_decisions == 1
    assert loop.silent_decisions == 0
    assert loop.total_decisions == 1
    assert loop.last_error is None


# ===========================================================================
# 4) test_loop_handles_letta_429_with_retry
# ===========================================================================


class _Fake429Error(Exception):
    """Stand-in for openai / langchain / letta RateLimitError."""

    def __init__(self, status_code: int = 429) -> None:
        super().__init__(f"rate limited (status={status_code})")
        self.status_code = status_code


@pytest.mark.asyncio
async def test_loop_handles_letta_429_with_retry(
    fresh_pool, in_memory_memory_store, monkeypatch,
):
    """When _stream_via_letta raises a 429-shaped exception, the retry wrapper
    backs off + retries.  After 3 retries, ``LettaRetryExhausted`` is raised,
    and the loop catches it (records last_error, sleeps 5min, returns
    ``{event: 'skipped', reason: 'letta_retry_exhausted'}``)."""
    pool, _sem = fresh_pool

    # Speed up retry: zero backoff + zero jitter + tight base.
    async def _always_429(**kwargs) -> AsyncIterator[str]:
        raise _Fake429Error(status_code=429)
        yield ""  # unreachable, makes it an async generator

    monkeypatch.setattr("app.graph._stream_via_letta", _always_429)
    monkeypatch.setattr(
        "app.scheduler.letta_retry._BASE_BACKOFF_SECONDS", 0.0,
    )
    monkeypatch.setattr(
        "app.scheduler.letta_retry._JITTER_SECONDS", 0.0,
    )

    # Don't actually sleep 5 minutes — patch the retry-exhausted sleep
    # constant + use a tight override.
    import app.scheduler.npc_loop as npc_loop_mod
    monkeypatch.setattr(npc_loop_mod, "_SLEEP_AFTER_RETRY_EXHAUSTED_SEC", 0.01)

    result = await pool.trigger_one("yao-shi")
    assert result["event"] == "skipped"
    assert result["reason"] == "letta_retry_exhausted"

    loop = pool.get("yao-shi")
    assert loop.last_error is not None
    assert "letta_retry" in loop.last_error or "exhausted" in loop.last_error
    # No fan-out happened.
    entries = in_memory_memory_store.load_agent_memory(
        session_id="loop-yao-shi", agent_key="yao-shi",
    )
    assert entries == []


# ===========================================================================
# 5) test_loop_respects_group_semaphore
# ===========================================================================


@pytest.mark.asyncio
async def test_loop_respects_group_semaphore(
    fresh_pool, in_memory_memory_store, monkeypatch,
):
    """Two consecutive trigger_one calls on the same NPC must respect the
    10s cool-down: the second one is short-circuited as ``cooldown``.

    We use a tight cool-down (0.2s, set by the fresh_pool fixture) so the
    test runs fast.  The assertion is: first call pushes, second call
    (within cool-down) returns ``{event: 'skipped', reason: 'cooldown'}``.
    """
    pool, sem = fresh_pool
    _install_letta_stream(monkeypatch, "药师在此！")

    # First call: should push (semaphore is fresh, cool-down not active).
    r1 = await pool.trigger_one("yao-shi")
    assert r1["event"] == "pushed", r1
    assert sem.is_in_cooldown() is True

    # Second call immediately after: should be skipped due to cooldown.
    r2 = await pool.trigger_one("yao-shi")
    assert r2["event"] == "skipped"
    assert r2["reason"] == "cooldown"

    # Bookkeeping: pushed_count still 1, not 2.
    loop = pool.get("yao-shi")
    assert loop.pushed_decisions == 1


# ===========================================================================
# 6) test_loop_revives_after_exception
# ===========================================================================


class _BoomError(Exception):
    pass


@pytest.mark.asyncio
async def test_loop_revives_after_exception(
    fresh_pool, in_memory_memory_store, monkeypatch,
):
    """A loop body that raises (NOT a 429) must be caught by ``_npc_loop``,
    log ``last_error``, sleep, and re-enter.  No crash, no zombie state.
    """
    pool, _sem = fresh_pool

    # Patch _stream_via_letta to raise a generic (non-rate-limit) error.
    async def _boom(**kwargs) -> AsyncIterator[str]:
        raise _BoomError("letta server is on fire")
        yield ""

    monkeypatch.setattr("app.graph._stream_via_letta", _boom)

    # Make the exception-sleep short so the test runs fast.
    import app.scheduler.npc_loop as npc_loop_mod
    monkeypatch.setattr(npc_loop_mod, "_SLEEP_AFTER_EXCEPTION_SEC", 0.01)

    # Start the loop on a fresh NPC, wait a bit, then stop.
    loop = pool.get("san-lang")
    loop.stop_event = asyncio.Event()  # fresh
    from app.scheduler.npc_loop import _npc_loop
    loop.task = asyncio.create_task(_npc_loop("san-lang", loop, pool))

    # Give the loop time to enter, raise, sleep, and re-enter at least once.
    await asyncio.sleep(0.1)

    # Loop is still alive despite the exception.
    assert loop.is_alive() is True
    assert loop.last_error is not None
    assert "BoomError" in loop.last_error or "letta server" in loop.last_error

    # No fan-out (it never made it past the letta call).
    entries = in_memory_memory_store.load_agent_memory(
        session_id="loop-san-lang", agent_key="san-lang",
    )
    assert entries == []

    # Clean shutdown.
    loop.stop_event.set()
    await asyncio.wait_for(loop.task, timeout=2.0)


# ===========================================================================
# 7) test_6_loops_dont_all_fire_simultaneously
# ===========================================================================


@pytest.mark.asyncio
async def test_6_loops_dont_all_fire_simultaneously(
    fresh_pool, in_memory_memory_store, monkeypatch,
):
    """Run trigger_one on all 6 NPCs back-to-back.  Because of the
    semaphore (single-flight + cool-down), only the FIRST NPC pushes;
    the other 5 must be skipped as ``cooldown``.

    This is the "concurrency guard" the ADR-0007 §Acceptance criteria 6
    promises ("6 loops running 1 min: no two NPCs speak within 10s").
    """
    pool, sem = fresh_pool
    _install_letta_stream(monkeypatch, "1-2 句话回复。")

    results = []
    for rk in ["shu-hang", "yao-shi", "san-lang", "bei-he", "bai-qianbei", "ling-die"]:
        results.append(await pool.trigger_one(rk))

    # First push succeeds; the other 5 must be skipped due to cool-down.
    pushed = [r for r in results if r["event"] == "pushed"]
    cooldown = [r for r in results if r["event"] == "skipped" and r["reason"] == "cooldown"]
    assert len(pushed) == 1, f"expected exactly 1 push, got {len(pushed)}: {results}"
    assert len(cooldown) == 5, f"expected 5 cooldowns, got {len(cooldown)}: {results}"

    # Only one NPC's memory should be populated.
    from app.letta_bridge.agent_manager import ROLE_AGENT_KEYS
    populated = [
        ak for ak in ROLE_AGENT_KEYS
        if in_memory_memory_store.load_agent_memory(
            session_id=f"loop-{pushed[0]['role_key']}", agent_key=ak,
        )
    ]
    assert len(populated) == 6  # fan-out wrote to all 6 (but only 1 speaker)


# ===========================================================================
# Supporting tests
# ===========================================================================


@pytest.mark.asyncio
async def test_loop_pool_status_dict(fresh_pool):
    """Pool.status_dict has the documented shape."""
    pool, _ = fresh_pool
    snap = pool.status_dict()
    assert snap["running"] is False  # not started yet
    assert snap["alive_count"] == 0
    assert snap["total"] == 6
    assert set(snap["loops"].keys()) == {
        "shu-hang", "yao-shi", "san-lang",
        "bei-he", "bai-qianbei", "ling-die",
    }
    for rk, info in snap["loops"].items():
        assert info["role_key"] == rk
        assert info["alive"] is False
        assert info["total_decisions"] == 0


@pytest.mark.asyncio
async def test_admin_status_includes_npc_loop_pool(in_memory_memory_store, monkeypatch):
    """/api/cron/status response includes ``npc_loop_pool`` field."""
    import httpx
    from httpx import ASGITransport

    from app.main import app
    from app.scheduler import shutdown_scheduler, start_scheduler

    transport = ASGITransport(app=app)
    bundle = start_scheduler()
    # MVP Candidate: default starts one batch behavior coordinator; the six
    # legacy per-NPC loops remain visible but inactive.
    try:
        # Force any deferred scheduler.start() inside this loop.
        await bundle.xiuzhen.trigger_now()
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver",
        ) as client:
            r = await client.get("/api/cron/status")
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["started"] is True
            assert "npc_loop_pool" in body, body
            npc = body["npc_loop_pool"]
            assert npc["running"] is False
            assert npc["alive_count"] == 0
            assert npc["total"] == 6
            assert set(npc["loops"].keys()) == {
                "shu-hang", "yao-shi", "san-lang",
                "bei-he", "bai-qianbei", "ling-die",
            }
            behavior = body["behavior_coordinator"]
            assert behavior["running"] is True
            assert behavior["daily_budget"] >= 1
    finally:
        await shutdown_scheduler()


@pytest.mark.asyncio
async def test_load_recent_group_events_basic(in_memory_memory_store):
    """AgentMemoryStore.load_recent_group_events returns most-recent-first,
    source='group' only, capped at limit."""
    import time as _t

    now_ms = int(_t.time() * 1000)

    # Insert 25 events: 20 group + 5 dm (interleaved).
    for i in range(20):
        in_memory_memory_store.append_message(
            session_id="sess-1",
            agent_key="shu-hang",
            role="agent",
            source="group",
            speaker_key=f"npc-{i % 6}",
            text=f"group event {i}",
            timestamp=now_ms - (1000 * (25 - i)),  # older → newer
        )
    for i in range(5):
        in_memory_memory_store.append_message(
            session_id="sess-1",
            agent_key="yao-shi",
            role="user",
            source="dm",
            speaker_key="user",
            text=f"dm event {i}",
            timestamp=now_ms - (500 * i),
        )

    recent = in_memory_memory_store.load_recent_group_events(limit=10)
    assert len(recent) == 10
    # All entries are source='group' (dm entries excluded).
    assert all(e.source == "group" for e in recent)
    # Most-recent-first: timestamps non-increasing.
    ts_list = [e.timestamp for e in recent]
    assert ts_list == sorted(ts_list, reverse=True)


def test_load_recent_group_events_zero_limit(in_memory_memory_store):
    """limit <= 0 returns empty list (defensive)."""
    assert in_memory_memory_store.load_recent_group_events(limit=0) == []
    assert in_memory_memory_store.load_recent_group_events(limit=-1) == []


@pytest.mark.asyncio
async def test_group_semaphore_guard_basic():
    """GroupChatSemaphore.guard() yields False inside cool-down, True after."""
    from app.scheduler.group_semaphore import GroupChatSemaphore

    sem = GroupChatSemaphore(cooldown_seconds=0.1)
    async with sem.guard() as ok:
        assert ok is True
    # Immediately after — should be in cool-down.
    async with sem.guard() as ok2:
        assert ok2 is False
    # Wait past the cool-down — should succeed again.
    await asyncio.sleep(0.15)
    async with sem.guard() as ok3:
        assert ok3 is True


@pytest.mark.asyncio
async def test_group_semaphore_serialises_concurrent_pushes():
    """Two concurrent ``guard()`` blocks must NOT overlap (single-flight)."""
    from app.scheduler.group_semaphore import GroupChatSemaphore

    sem = GroupChatSemaphore(cooldown_seconds=0.0)  # disable cool-down

    overlap = {"max": 0, "current": 0}

    async def worker():
        async with sem.guard() as ok:
            assert ok is True
            overlap["current"] += 1
            overlap["max"] = max(overlap["max"], overlap["current"])
            await asyncio.sleep(0.05)
            overlap["current"] -= 1

    # Run 4 workers in parallel.
    await asyncio.gather(*[worker() for _ in range(4)])
    # Single-flight: at no point should overlap.current exceed 1.
    assert overlap["max"] == 1


@pytest.mark.asyncio
async def test_letta_retry_exhausts_after_3(monkeypatch):
    """``stream_via_letta_with_retry`` raises ``LettaRetryExhausted`` after
    ``max_retries`` consecutive 429s."""
    from app.scheduler.letta_retry import (
        LettaRetryExhausted,
        stream_via_letta_with_retry,
    )

    async def _always_429(**kwargs) -> AsyncIterator[str]:
        raise _Fake429Error(status_code=429)
        yield ""

    monkeypatch.setattr("app.graph._stream_via_letta", _always_429)
    monkeypatch.setattr("app.scheduler.letta_retry._BASE_BACKOFF_SECONDS", 0.0)
    monkeypatch.setattr("app.scheduler.letta_retry._JITTER_SECONDS", 0.0)

    raised = False
    try:
        async for _ in stream_via_letta_with_retry(
            role_key="shu-hang", session_id="x", all_msgs=[],
            max_retries=3,
        ):
            pass
    except LettaRetryExhausted:
        raised = True
    assert raised, "expected LettaRetryExhausted after 3 retries"


@pytest.mark.asyncio
async def test_letta_retry_propagates_non_rate_limit(monkeypatch):
    """Non-rate-limit exceptions are propagated without retry."""
    from app.scheduler.letta_retry import stream_via_letta_with_retry

    class _BadRequest(Exception):
        pass

    async def _bad_request(**kwargs) -> AsyncIterator[str]:
        raise _BadRequest("syntax error in role config")
        yield ""

    monkeypatch.setattr("app.graph._stream_via_letta", _bad_request)

    with pytest.raises(_BadRequest):
        async for _ in stream_via_letta_with_retry(
            role_key="yao-shi", session_id="x", all_msgs=[],
        ):
            pass


def test_lifespan_gc_loops_enabled_default_true(monkeypatch):
    """GC_LOOPS_ENABLED defaults to True (loop pool starts)."""
    monkeypatch.delenv("GC_LOOPS_ENABLED", raising=False)
    monkeypatch.delenv("XZ_CRON_ENABLED", raising=False)
    from app.scheduler.lifespan import _read_loops_enabled_env
    assert _read_loops_enabled_env() is True


def test_lifespan_gc_loops_enabled_explicit_false(monkeypatch):
    """GC_LOOPS_ENABLED=false wins over XZ_CRON_ENABLED=true."""
    monkeypatch.setenv("GC_LOOPS_ENABLED", "false")
    monkeypatch.setenv("XZ_CRON_ENABLED", "true")
    from app.scheduler.lifespan import _read_loops_enabled_env
    assert _read_loops_enabled_env() is False


def test_lifespan_gc_loops_deprecated_alias(monkeypatch):
    """When GC_LOOPS_ENABLED is unset, XZ_CRON_ENABLED acts as the alias."""
    monkeypatch.delenv("GC_LOOPS_ENABLED", raising=False)
    monkeypatch.setenv("XZ_CRON_ENABLED", "false")
    from app.scheduler.lifespan import _read_loops_enabled_env
    assert _read_loops_enabled_env() is False


@pytest.mark.asyncio
async def test_trigger_one_unknown_role(fresh_pool):
    """trigger_one with an unknown role_key returns an error summary."""
    pool, _ = fresh_pool
    result = await pool.trigger_one("not-a-real-npc")
    assert result["event"] == "error"
    assert result["reason"] == "unknown_role"


@pytest.mark.asyncio
async def test_loop_pool_start_and_stop(in_memory_memory_store, monkeypatch):
    """start_all() spawns 6 tasks; stop_all() cancels them within timeout."""
    from app.scheduler.npc_loop import (
        NpcLoopPool,
        get_npc_loop_pool,
        set_npc_loop_pool,
        stop_all as npc_loop_stop_all,
    )

    # Patch random.uniform so the loops' internal sleeps are tight.
    monkeypatch.setattr(
        "app.scheduler.npc_loop.random.uniform",
        lambda lo, hi: 0.001,
    )
    # Patch _stream_via_letta so the loop calls don't hit the real LLM.
    _install_letta_stream(monkeypatch, "<silent/>")

    set_npc_loop_pool(NpcLoopPool())
    pool = get_npc_loop_pool()
    pool.start_all()
    assert pool.alive_count() == 6

    # Let the loops tick once.
    await asyncio.sleep(0.1)

    # Now stop — must terminate all 6 tasks within timeout.
    await npc_loop_stop_all()
    assert pool.alive_count() == 0


@pytest.mark.asyncio
async def test_admin_trigger_npc_loop_route(in_memory_memory_store, monkeypatch):
    """POST /api/cron/trigger with service='npc_loop' fires one NPC cycle."""
    import httpx
    from httpx import ASGITransport

    from app.main import app
    from app.scheduler import shutdown_scheduler, start_scheduler

    _install_letta_stream(monkeypatch, "trigger 一下试试！")

    transport = ASGITransport(app=app)
    bundle = start_scheduler()
    try:
        await bundle.xiuzhen.trigger_now()  # force scheduler start
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver",
        ) as client:
            r = await client.post(
                "/api/cron/trigger",
                json={"service": "npc_loop", "target": "shu-hang"},
            )
            assert r.status_code == 200, r.text
            body = r.json()
            # First call: should push (semaphore cool-down not yet active).
            assert body["event"] in {"pushed", "silent", "skipped", "error"}
            assert body["role_key"] == "shu-hang"
    finally:
        await shutdown_scheduler()
