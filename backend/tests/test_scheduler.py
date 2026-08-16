"""Stage 8 Cron — scheduler tests.

Tests the APScheduler-driven cron services added in commit (cron-auto-post):

1.  test_lifespan_starts_and_stops_scheduler          — lifespan integration
2.  test_xiuzhen_cron_trigger_calls_stream_via_letta   — core fire path
3.  test_xiuzhen_cron_records_fire_count               — state bookkeeping
4.  test_xiuzhen_cron_throttles_same_npc               — 1h same-NPC throttle
5.  test_xiuzhen_cron_picks_all_6_npcs_evenly          — uniform sampling
6.  test_xiuzhen_cron_disabled_skips                   — global enable flag
7.  test_xiuzhen_cron_empty_active_session_ok          — graceful degrade (no WS)
8.  test_dm_followup_scan_idle_pairs                  — DM scan logic
9.  test_dm_followup_fire_persists_to_memory_store     — DM persistence
10. test_admin_toggle_and_status_endpoints             — /api/cron routes
11. test_pytest_collect_only_parses_cron_args          — env var contract
12. test_connection_registry_basic                     — registry bookkeeping

The tests mock ``app.graph._stream_via_letta`` (the heavy Letta I/O
hot path) so they run in ~0.2s total without needing a real Letta server.
We do not rely on the real ``AsyncIOScheduler`` — each service is
constructed with ``start()`` skipped and ``trigger_now()`` called directly.

IMPORTANT: the ``fake_letta_stream`` fixture uses ``monkeypatch`` so the
patch is reverted at the end of each test — this prevents leaks into
the other test files (test_graph.py / test_letta_integration.py)
that exercise the real ``_stream_via_letta``.
"""
from __future__ import annotations

import time
from typing import Any, AsyncIterator
from unittest.mock import AsyncMock, MagicMock

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _fake_stream_via_letta(
    *,
    role_key: str,
    session_id: str,
    all_msgs: list[Any],
) -> AsyncIterator[str]:
    """Fake stream that yields 1-2 chunks based on role_key.

    The fake intentionally returns DIFFERENT text per role so tests can
    verify the right role was called.
    """
    yield f"[{role_key} 主动发言]"
    yield f" 来自 cron ({session_id})"


@pytest.fixture(autouse=True)
def _reset_singletons():
    """Each test gets a fresh ConnectionRegistry + CronState.

    Avoids cross-test pollution from the process-wide singletons.
    """
    from app.scheduler.connection_registry import set_connection_registry
    from app.scheduler.dm_followup import set_dm_followup_service
    from app.scheduler.npc_loop import set_npc_loop_pool
    from app.scheduler.state import reset_cron_state_for_tests
    from app.scheduler.xiuzhen_cron import set_xiuzhen_cron_service

    reset_cron_state_for_tests()
    set_connection_registry(None)
    set_xiuzhen_cron_service(None)
    set_dm_followup_service(None)
    set_npc_loop_pool(None)
    yield
    reset_cron_state_for_tests()
    set_connection_registry(None)
    set_xiuzhen_cron_service(None)
    set_dm_followup_service(None)
    set_npc_loop_pool(None)


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
def fake_letta_stream(monkeypatch):
    """Patch _stream_via_letta with the module-level fake.

    Uses ``monkeypatch`` so the patch auto-reverts at end of test —
    this prevents leaking into other test files (test_graph.py /
    test_letta_integration.py) which test the real function.
    """
    captured: list[dict[str, Any]] = []

    async def _capture(**kwargs):
        captured.append(kwargs)
        async for piece in _fake_stream_via_letta(**kwargs):
            yield piece

    monkeypatch.setattr("app.scheduler.xiuzhen_cron._stream_via_letta", _capture)
    monkeypatch.setattr("app.scheduler.dm_followup._stream_via_letta", _capture)
    return captured


# ===========================================================================
# 1) Lifespan start/stop
# ===========================================================================


@pytest.mark.asyncio
async def test_event_coordinator_keeps_legacy_group_cron_dormant(monkeypatch):
    """Default startup has one proactive policy, never coordinator + random cron."""
    from app.scheduler import shutdown_scheduler, start_scheduler

    monkeypatch.delenv("GC_LOOPS_ENABLED", raising=False)
    monkeypatch.delenv("XZ_CRON_ENABLED", raising=False)
    bundle = start_scheduler()
    try:
        assert bundle.behavior_coordinator.is_running() is True
        assert bundle.behavior_coordinator.state.enabled is True
        assert bundle.xiuzhen.is_running() is False
    finally:
        await shutdown_scheduler()


@pytest.mark.asyncio
async def test_lifespan_starts_and_stops_scheduler():
    """``start_scheduler`` constructs both services and starts them; ``shutdown_scheduler``
    reverses cleanly.  Idempotent.

    Runs inside an asyncio event loop (decorated) so the deferred
    AsyncIOScheduler.start() can find a running loop on first trigger_now().
    """
    from app.scheduler import shutdown_scheduler, start_scheduler
    from app.scheduler.lifespan import get_scheduler

    bundle = start_scheduler()
    assert bundle is not None
    assert bundle.xiuzhen is not None
    assert bundle.dm_followup is not None

    # Idempotent — second start returns same bundle, doesn't double-start.
    bundle2 = start_scheduler()
    assert bundle2 is bundle

    # Force any deferred scheduler.start() to run inside this event loop.
    await bundle.xiuzhen.trigger_now()
    await bundle.dm_followup.trigger_now()

    # Now the schedulers are running.
    assert bundle.xiuzhen.is_running() is True
    assert bundle.dm_followup.is_running() is True

    # status_dict has the right shape.
    snap = bundle.xiuzhen.status_dict()
    assert snap["running"] is True
    assert "interval_min" in snap
    assert "enabled" in snap

    # Shutdown reverses.
    await shutdown_scheduler()
    assert get_scheduler() is None


# ===========================================================================
# 2) Xiuzhen cron — core fire path
# ===========================================================================


@pytest.mark.asyncio
async def test_xiuzhen_cron_trigger_calls_stream_via_letta(
    fake_letta_stream: list[dict[str, Any]],
    in_memory_memory_store,
):
    """``trigger_now()`` must call ``_stream_via_letta`` with one chosen NPC's
    role_key and a system-style user_msg, and must increment the fire counter."""
    from app.scheduler.state import get_cron_state
    from app.scheduler.xiuzhen_cron import XiuzhenCronService

    svc = XiuzhenCronService(interval_min=5, enabled=True)
    # Do NOT call start() — that would schedule a real timer.
    result = await svc.trigger_now()

    assert result["event"] == "fired", result
    assert result["role_key"] in {
        "shu-hang", "yao-shi", "san-lang",
        "bei-he", "bai-qianbei", "ling-die",
    }
    assert len(fake_letta_stream) == 1
    call = fake_letta_stream[0]
    assert call["role_key"] == result["role_key"]
    assert call["session_id"] == f"cron-{result['role_key']}"
    # Bookkeeping: counter incremented.
    state = get_cron_state()
    assert state.group_fire_count == 1
    assert state.last_group_fire_at is not None


# ===========================================================================
# 3) Fire count bookkeeping (already covered partly above; add isolated test)
# ===========================================================================


@pytest.mark.asyncio
async def test_xiuzhen_cron_records_fire_count(
    fake_letta_stream: list[dict[str, Any]],
    in_memory_memory_store,
):
    from app.scheduler.state import get_cron_state
    from app.scheduler.xiuzhen_cron import XiuzhenCronService

    svc = XiuzhenCronService(interval_min=5, enabled=True)
    for _ in range(3):
        await svc.trigger_now()
    state = get_cron_state()
    assert state.group_fire_count == 3


# ===========================================================================
# 4) Throttle: same NPC doesn't fire twice within 1h
# ===========================================================================


@pytest.mark.asyncio
async def test_xiuzhen_cron_throttles_same_npc(
    fake_letta_stream: list[dict[str, Any]],
    in_memory_memory_store,
):
    """After firing NPC X, calling trigger_now() immediately again must NOT
    fire X a second time (the 1h throttle kicks in)."""
    from app.scheduler.xiuzhen_cron import XiuzhenCronService

    # Restrict to a single NPC so the pool is deterministic.
    svc = XiuzhenCronService(interval_min=5, enabled=True, npc_filter=["bai-qianbei"])

    r1 = await svc.trigger_now()
    assert r1["event"] == "fired"
    assert r1["role_key"] == "bai-qianbei"

    r2 = await svc.trigger_now()
    # The pool should be empty (throttled) → skipped.
    assert r2["event"] == "skipped"
    assert r2["reason"] == "no_eligible_npc"
    # Exactly 1 Letta call.
    assert len(fake_letta_stream) == 1


# ===========================================================================
# 5) 6 NPCs roughly uniformly sampled (mock 100 times)
# ===========================================================================


@pytest.mark.asyncio
async def test_xiuzhen_cron_picks_all_6_npcs_evenly(
    fake_letta_stream: list[dict[str, Any]],
    in_memory_memory_store,
):
    """Run 100 fires; every NPC must appear at least 6 times (uniform sampling)."""
    from app.scheduler.xiuzhen_cron import XiuzhenCronService

    svc = XiuzhenCronService(interval_min=5, enabled=True)
    # Reset throttle map so all 6 NPCs are eligible each tick.
    state = svc.state
    state.last_fire_at.clear()

    counts: dict[str, int] = {}
    for _ in range(100):
        # clear the throttle between ticks (simulate "well-spaced" fires)
        state.last_fire_at.clear()
        result = await svc.trigger_now()
        assert result["event"] == "fired", result
        counts[result["role_key"]] = counts.get(result["role_key"], 0) + 1

    # All 6 NPCs sampled; expect ~16-17 each (uniform).
    assert set(counts.keys()) == {
        "shu-hang", "yao-shi", "san-lang",
        "bei-he", "bai-qianbei", "ling-die",
    }
    # With 100 samples uniform over 6, each should be >= 6 (statistically
    # the lower bound is well above this).
    for k, v in counts.items():
        assert v >= 6, f"{k} sampled only {v} times in 100 runs — not uniform"


# ===========================================================================
# 6) Global disable flag
# ===========================================================================


@pytest.mark.asyncio
async def test_xiuzhen_cron_disabled_skips(
    fake_letta_stream: list[dict[str, Any]],
    in_memory_memory_store,
):
    """When ``state.enabled=False``, ``trigger_now()`` must NOT fire."""
    from app.scheduler.xiuzhen_cron import XiuzhenCronService

    svc = XiuzhenCronService(interval_min=5, enabled=False)
    result = await svc.trigger_now()
    assert result["event"] == "skipped"
    assert result["reason"] == "disabled"
    assert len(fake_letta_stream) == 0


# ===========================================================================
# 7) Empty active WS sessions — graceful degrade
# ===========================================================================


@pytest.mark.asyncio
async def test_xiuzhen_cron_empty_active_session_ok(
    fake_letta_stream: list[dict[str, Any]],
    in_memory_memory_store,
):
    """No WS clients connected → cron still fires; ws_pushed=0; no error."""
    from app.scheduler.xiuzhen_cron import XiuzhenCronService

    svc = XiuzhenCronService(interval_min=5, enabled=True)
    result = await svc.trigger_now()
    assert result["event"] == "fired"
    assert result["ws_pushed"] == 0


# ===========================================================================
# 8) DM followup — scan idle pairs
# ===========================================================================


@pytest.mark.asyncio
async def test_dm_followup_scan_idle_pairs(
    fake_letta_stream: list[dict[str, Any]],
    in_memory_memory_store,
):
    """scan_idle_pairs must return (sid, role) pairs whose latest entry is
    > idle_hour ago AND that have at least one DM user entry."""
    from app.scheduler.dm_followup import DmFollowupService

    # Insert 25h-old DM entry for shu-hang/sess-A.
    now_ms = int(time.time() * 1000)
    long_ago_ms = now_ms - (25 * 3600 * 1000)
    in_memory_memory_store.append_message(
        session_id="sess-A",
        agent_key="shu-hang",
        role="user",
        source="dm",
        speaker_key="user",
        text="hi shu-hang",
        timestamp=long_ago_ms,
    )

    # Insert 25h-old GROUP-only entry for yao-shi/sess-B → should NOT match
    # (no DM entry).
    in_memory_memory_store.append_message(
        session_id="sess-B",
        agent_key="yao-shi",
        role="agent",
        source="group",
        speaker_key="yao-shi",
        text="hi group",
        timestamp=long_ago_ms,
        agent_name="药师",
        agent_emoji="💊",
    )

    # Insert 1h-old DM entry for ling-die/sess-C → should NOT match (too recent).
    in_memory_memory_store.append_message(
        session_id="sess-C",
        agent_key="ling-die",
        role="user",
        source="dm",
        speaker_key="user",
        text="hi lingdie",
        timestamp=now_ms - (1 * 3600 * 1000),
    )

    svc = DmFollowupService(idle_hour=24.0, interval_hour=1.0)
    pairs = svc.scan_idle_pairs()
    assert pairs == [("sess-A", "shu-hang")], pairs


# ===========================================================================
# 9) DM followup — fire persists to memory store
# ===========================================================================


@pytest.mark.asyncio
async def test_dm_followup_fire_persists_to_memory_store(
    fake_letta_stream: list[dict[str, Any]],
    in_memory_memory_store,
):
    """After scan + fire, the agent's reply must be appended to the DM timeline."""
    from app.scheduler.dm_followup import DmFollowupService

    now_ms = int(time.time() * 1000)
    long_ago_ms = now_ms - (25 * 3600 * 1000)
    in_memory_memory_store.append_message(
        session_id="sess-DM",
        agent_key="bei-he",
        role="user",
        source="dm",
        speaker_key="user",
        text="北河前辈在吗",
        timestamp=long_ago_ms,
    )

    svc = DmFollowupService(idle_hour=24.0, interval_hour=1.0)
    summary = await svc.trigger_now()
    assert summary["event"] == "fired"
    assert summary["succeeded"] == 1

    # Memory store now has the user msg + the agent reply (DM source).
    entries = in_memory_memory_store.load_agent_memory("sess-DM", "bei-he")
    assert len(entries) == 2
    assert entries[0].role == "user"
    assert entries[1].role == "agent"
    assert entries[1].source == "dm"
    assert entries[1].speaker_key == "bei-he"


# ===========================================================================
# 10) Admin endpoints — status + toggle
# ===========================================================================


@pytest.mark.asyncio
async def test_admin_toggle_and_status_endpoints(
    fake_letta_stream: list[dict[str, Any]],
    in_memory_memory_store,
):
    """Drive the FastAPI app via httpx ASGI transport (async-safe TestClient);
    toggle the cron and read the status.  /api/cron/status must return 200 +
    the expected shape.

    Uses ``httpx.AsyncClient(transport=ASGITransport(app=app))`` so the
    lifespan event loop is properly driven (the sync TestClient can race
    with APScheduler's loop detection).
    """
    import httpx
    from httpx import ASGITransport

    from app.main import app
    from app.scheduler import shutdown_scheduler, start_scheduler

    transport = ASGITransport(app=app)
    # Start the scheduler before the transport (in the current loop).
    bundle = start_scheduler()

    try:
        # Drive the lifespan via the AsyncClient context.
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver",
        ) as real_client:
                # The status is visible via start_scheduler's bundle BEFORE
                # any HTTP call (the bundle was started in this loop).
                await bundle.xiuzhen.trigger_now()  # ensure scheduler started
                r = await real_client.get("/api/cron/status")
                assert r.status_code == 200, r.text
                body = r.json()
                assert body["started"] is True
                assert body["xiuzhen"] is not None
                assert body["xiuzhen"]["running"] is True
                assert body["xiuzhen"]["enabled"] is True
                assert "interval_min" in body["xiuzhen"]
                assert "next_fire_time" in body["xiuzhen"]

                # Toggle: disable cron + narrow to 2 NPCs.
                r2 = await real_client.post(
                    "/api/cron/toggle",
                    json={
                        "enabled": False,
                        "npc_filter": ["shu-hang", "bai-qianbei"],
                        "interval_min": 7,
                    },
                )
                assert r2.status_code == 200, r2.text
                body2 = r2.json()
                assert body2["xiuzhen"]["enabled"] is False
                assert body2["xiuzhen"]["npc_filter"] == ["shu-hang", "bai-qianbei"]
                assert body2["xiuzhen"]["interval_min"] == 7

                # Re-enable for clean teardown.
                await real_client.post("/api/cron/toggle", json={"enabled": True})
    finally:
        await shutdown_scheduler()


# ===========================================================================
# 11) pytest --collect-only parses cron-related env var contract
# ===========================================================================


def test_pytest_collect_only_parses_cron_args(monkeypatch):
    """Verify env vars are parsed (clamped, default fallback).

    This is a structural test: the contract that ``XZ_CRON_INTERVAL_MIN``
    is honoured (clamped to [1, 1440]) and that ``XZ_CRON_ENABLED=false``
    disables the cron.
    """
    # Default interval is 5 when env unset.
    monkeypatch.delenv("XZ_CRON_INTERVAL_MIN", raising=False)
    from app.scheduler.xiuzhen_cron import XiuzhenCronService
    svc1 = XiuzhenCronService()
    assert svc1.interval_min == 5

    # Below min → clamped to 1.
    monkeypatch.setenv("XZ_CRON_INTERVAL_MIN", "0")
    svc2 = XiuzhenCronService()
    assert svc2.interval_min == 1

    # Above max → clamped to 1440.
    monkeypatch.setenv("XZ_CRON_INTERVAL_MIN", "99999")
    svc3 = XiuzhenCronService()
    assert svc3.interval_min == 1440

    # Env-driven disable.
    monkeypatch.setenv("XZ_CRON_ENABLED", "false")
    svc4 = XiuzhenCronService()
    assert svc4.state.enabled is False

    # Also test the DM followup envs.
    monkeypatch.delenv("XZ_CRON_ENABLED", raising=False)
    monkeypatch.setenv("XZ_DM_FOLLOWUP_INTERVAL_HOUR", "0.001")
    monkeypatch.setenv("XZ_DM_FOLLOWUP_IDLE_HOUR", "48")
    from app.scheduler.dm_followup import DmFollowupService
    svc5 = DmFollowupService()
    assert svc5.interval_hour >= 0.016  # clamped to MIN
    assert svc5.idle_hour == 48.0


# ===========================================================================
# 12) ConnectionRegistry basic operations
# ===========================================================================


def test_connection_registry_basic():
    """ConnectionRegistry registers/unregisters sessions and exposes snapshots."""
    from app.scheduler.connection_registry import ConnectionRegistry

    reg = ConnectionRegistry()
    fake_ws_a = MagicMock(name="ws_a")
    fake_ws_b = MagicMock(name="ws_b")

    reg.register("sid-A", fake_ws_a)
    reg.register("sid-B", fake_ws_b)
    assert reg.active_count() == 2
    assert "sid-A" in reg
    assert "sid-B" in reg
    assert reg.get("sid-A") is fake_ws_a

    # unregister is silent on missing
    reg.unregister("sid-NONEXISTENT")
    reg.unregister("sid-A")
    assert reg.active_count() == 1
    assert "sid-A" not in reg

    # Snapshot order-agnostic
    assert sorted(reg.active_sessions()) == ["sid-B"]

    # clear drops all
    reg.clear()
    assert reg.active_count() == 0


# ===========================================================================
# Bonus: WS push when active sessions exist
# ===========================================================================


@pytest.mark.asyncio
async def test_xiuzhen_cron_pushes_to_active_ws(
    fake_letta_stream: list[dict[str, Any]],
    in_memory_memory_store,
):
    """When a WS session is registered, the cron must push a cron_agent_post event."""
    from app.scheduler.connection_registry import ConnectionRegistry, set_connection_registry
    from app.scheduler.xiuzhen_cron import XiuzhenCronService

    reg = ConnectionRegistry()
    fake_ws = MagicMock()
    fake_ws.send_json = AsyncMock()
    reg.register("sid-XYZ", fake_ws)
    set_connection_registry(reg)

    svc = XiuzhenCronService(interval_min=5, enabled=True)
    result = await svc.trigger_now()
    assert result["event"] == "fired"
    assert result["ws_pushed"] == 1
    fake_ws.send_json.assert_awaited_once()
    sent = fake_ws.send_json.await_args.args[0]
    assert sent["type"] == "cron_agent_post"
    assert sent["session_id"] == "sid-XYZ"
    assert "full_text" in sent["payload"]
    assert "role_key" in sent["payload"]
