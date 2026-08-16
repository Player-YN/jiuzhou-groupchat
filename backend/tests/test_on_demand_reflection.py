"""P0-A on-demand reflection service tests.

Stage 9 / P0-A — event-driven reflection (no scheduler, no daily cron).
6 tests, all using stub dependencies — **no real Letta, no real SQLite**.

Spec coverage:

1. ``test_on_user_message_short_no_trigger``   — text len 100 → None
2. ``test_on_user_message_long_triggers``      — text len 250 → event
3. ``test_on_silence_below_threshold_no_trigger`` — 30 min → None
4. ``test_on_silence_above_threshold_triggers``  — 90 min → event
5. ``test_on_self_over_mentioned_triggers``    — 3 mentions → event
6. ``test_llm_failure_returns_event_with_error`` — LLM raises → event w/ success=False

Stub pattern (per P0-A spec):

- ``letta_stream_fn`` is an ``async def`` generator that yields string chunks.
  We provide 2 variants: ``_stub_stream_success`` (yields "stub reply") and
  ``_stub_stream_raise`` (raises ``RuntimeError`` mid-stream).
- ``memory_store`` is a ``MagicMock`` with an ``append_message`` method
  (matches the spec's "a mock with append_message method").

Why MagicMock and not the real ``AgentMemoryStore``: the spec explicitly says
"Do not use real Letta or real SQLite."  We also don't want a 6-fan-out side
effect polluting a real DB during unit tests.

The service is event-driven (no scheduler) so we just construct a fresh
``OnDemandReflectionService`` per test with the stubs wired in.
"""
from __future__ import annotations

from typing import Any, AsyncIterator
from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# Helpers — stub LLM stream + stub memory store
# ---------------------------------------------------------------------------


async def _stub_stream_success(
    *,
    role_key: str,
    session_id: str,
    all_msgs: list[Any],
) -> AsyncIterator[str]:
    """Yield a single chunk 'stub reply' for happy-path tests.

    Signature matches ``app.scheduler.letta_retry.stream_via_letta_with_retry``
    keyword-only signature.
    """
    yield "stub reply"


async def _stub_stream_raise(
    *,
    role_key: str,
    session_id: str,
    all_msgs: list[Any],
) -> AsyncIterator[str]:
    """Raise ``RuntimeError`` BEFORE the first yield.

    This is the error path: a Letta HTTP 500 / network blip / model timeout
    that the service must catch and surface as ``success=False``.
    The ``yield ""`` is unreachable but required syntactically to make this
    function an async generator.
    """
    raise RuntimeError("simulated LLM outage")
    yield ""  # pragma: no cover — never reached, but keeps it a generator


@pytest.fixture
def stub_memory_store() -> MagicMock:
    """MagicMock with ``append_message`` method (per P0-A test spec)."""
    return MagicMock()


# ===========================================================================
# 1) test_on_user_message_short_no_trigger
# ===========================================================================
@pytest.mark.asyncio
async def test_on_user_message_short_no_trigger(stub_memory_store: MagicMock) -> None:
    """A 100-char user message is below the 200-char threshold → no trigger."""
    from app.scheduler.on_demand_reflection import OnDemandReflectionService

    svc = OnDemandReflectionService(
        memory_store=stub_memory_store,
        letta_stream_fn=_stub_stream_success,
    )
    msg = {"text": "x" * 100, "session_id": "sess-1"}
    result = await svc.on_user_message(msg)
    assert result is None
    # LLM + storage should NOT have been called.
    assert stub_memory_store.append_message.call_count == 0


# ===========================================================================
# 2) test_on_user_message_long_triggers
# ===========================================================================
@pytest.mark.asyncio
async def test_on_user_message_long_triggers(stub_memory_store: MagicMock) -> None:
    """A 250-char user message triggers a deep_user_input reflection."""
    from app.scheduler.on_demand_reflection import OnDemandReflectionService

    svc = OnDemandReflectionService(
        memory_store=stub_memory_store,
        letta_stream_fn=_stub_stream_success,
    )
    msg = {"text": "x" * 250, "session_id": "sess-1"}
    result = await svc.on_user_message(msg)
    assert result is not None
    assert result.event_type == "deep_user_input"
    assert result.success is True
    assert result.error is None
    assert result.result_text == "stub reply"
    assert result.role_key in {
        "shu-hang", "yao-shi", "san-lang", "bei-he", "bai-qianbei", "ling-die",
    }
    # LLM + storage should have been called exactly once.
    assert stub_memory_store.append_message.call_count == 1
    append_kwargs = stub_memory_store.append_message.call_args.kwargs
    assert append_kwargs["session_id"] == "sess-1"
    assert append_kwargs["role"] == "agent"
    assert append_kwargs["source"] == "group"
    assert append_kwargs["speaker_key"] == "system"


# ===========================================================================
# 3) test_on_silence_below_threshold_no_trigger
# ===========================================================================
@pytest.mark.asyncio
async def test_on_silence_below_threshold_no_trigger(
    stub_memory_store: MagicMock,
) -> None:
    """30-minute silence is below the 60-min threshold → no trigger."""
    from app.scheduler.on_demand_reflection import OnDemandReflectionService

    svc = OnDemandReflectionService(
        memory_store=stub_memory_store,
        letta_stream_fn=_stub_stream_success,
    )
    result = await svc.on_silence("sess-2", silence_duration_min=30)
    assert result is None
    assert stub_memory_store.append_message.call_count == 0


# ===========================================================================
# 4) test_on_silence_above_threshold_triggers
# ===========================================================================
@pytest.mark.asyncio
async def test_on_silence_above_threshold_triggers(
    stub_memory_store: MagicMock,
) -> None:
    """90-minute silence triggers a long_silence reflection."""
    from app.scheduler.on_demand_reflection import OnDemandReflectionService

    svc = OnDemandReflectionService(
        memory_store=stub_memory_store,
        letta_stream_fn=_stub_stream_success,
    )
    result = await svc.on_silence("sess-2", silence_duration_min=90)
    assert result is not None
    assert result.event_type == "long_silence"
    assert result.success is True
    assert result.session_id == "sess-2"
    assert result.role_key in {
        "shu-hang", "yao-shi", "san-lang", "bei-he", "bai-qianbei", "ling-die",
    }
    # Prompt should mention the silence minutes (90).
    assert "90" in result.prompt
    assert stub_memory_store.append_message.call_count == 1


# ===========================================================================
# 5) test_on_self_over_mentioned_triggers
# ===========================================================================
@pytest.mark.asyncio
async def test_on_self_over_mentioned_triggers(
    stub_memory_store: MagicMock,
) -> None:
    """3+ @-mentions in 10 min triggers a self_over_mentioned reflection."""
    from app.scheduler.on_demand_reflection import OnDemandReflectionService

    svc = OnDemandReflectionService(
        memory_store=stub_memory_store,
        letta_stream_fn=_stub_stream_success,
    )
    result = await svc.on_self_over_mentioned(
        "sess-3", role_key="shu-hang", mention_count_10min=3,
    )
    assert result is not None
    assert result.event_type == "self_over_mentioned"
    assert result.success is True
    assert result.role_key == "shu-hang"  # explicit target preserved
    assert result.session_id == "sess-3"
    # Prompt should include the count.
    assert "3" in result.prompt
    assert stub_memory_store.append_message.call_count == 1


# ===========================================================================
# 6) test_llm_failure_returns_event_with_error
# ===========================================================================
@pytest.mark.asyncio
async def test_llm_failure_returns_event_with_error(
    stub_memory_store: MagicMock,
) -> None:
    """When the LLM stream raises, the service must still return a fully-
    populated ``ReflectionEvent`` with ``success=False`` and ``error`` set.

    The event MUST NOT be None (per spec: "method returns the event (NOT
    None) so callers know the trigger fired even if LLM failed").
    """
    from app.scheduler.on_demand_reflection import OnDemandReflectionService

    svc = OnDemandReflectionService(
        memory_store=stub_memory_store,
        letta_stream_fn=_stub_stream_raise,  # raises RuntimeError
    )
    msg = {"text": "y" * 250, "session_id": "sess-fail"}
    result = await svc.on_user_message(msg)
    assert result is not None
    assert result.event_type == "deep_user_input"
    assert result.success is False
    assert result.error is not None
    # Error should mention the LLM (not storage) since the LLM step ran first.
    assert "llm" in result.error
    assert "RuntimeError" in result.error
    assert "simulated LLM outage" in result.error
    # Storage should NOT have been called (we skip storage when LLM fails).
    assert stub_memory_store.append_message.call_count == 0


# ===========================================================================
# Bonus: status_dict counter correctness (light smoke; not in spec list)
# ===========================================================================
@pytest.mark.asyncio
async def test_status_dict_counts_each_event_type(
    stub_memory_store: MagicMock,
) -> None:
    """status_dict returns accurate ``total_reflections`` + ``by_type`` counts.

    Drive one event of each type (all happy path) and assert the counters
    match.  This is a light smoke that wasn't in the spec test list but
    matters for the future ``/api/reflection/status`` endpoint.
    """
    from app.scheduler.on_demand_reflection import OnDemandReflectionService

    svc = OnDemandReflectionService(
        memory_store=stub_memory_store,
        letta_stream_fn=_stub_stream_success,
    )
    # deep_user_input (long msg)
    await svc.on_user_message({"text": "z" * 250, "session_id": "s"})
    # long_silence (>= 60 min)
    await svc.on_silence("s", silence_duration_min=70)
    # self_over_mentioned
    await svc.on_self_over_mentioned("s", role_key="yao-shi", mention_count_10min=5)

    status = svc.status_dict()
    assert status["total_reflections"] == 3
    assert status["by_type"]["deep_user_input"] == 1
    assert status["by_type"]["long_silence"] == 1
    assert status["by_type"]["self_over_mentioned"] == 1
    assert status["last_error"] is None
