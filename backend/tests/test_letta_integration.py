"""Stage 7 — Letta v0.16.8 integration tests.

Scope (per `AGENTS.md` Stage 7 spec):
  1. LettaClient hits `/v1/health`, `/v1/agents`, `/v1/agents/{id}` via
     a `MockTransport` (no real Letta server required).
  2. LettaAgentRegistry.bootstrap_all creates exactly 6 NPCs (one per
     `ROLES` key) when registry is empty.
  3. LettaAgentRegistry.bootstrap_all is idempotent — second run
     reports all 6 as `reused`.
  4. Per-NPC memory_block payload contains the role's full `system`
     text under `persona` label.
  5. `_stream_via_letta` decodes a fake SSE stream into ordered
     `content` pieces (which `stream_dm_chat` would forward as
     `dm_msg_chunk` events).

These tests do NOT need a real Letta server — they inject a mock
transport via `LettaClient.set_test_transport(...)` so the HTTP
client behaviour is fully deterministic.  Real Letta integration is
verified separately by `tests/probe_letta_e2e.py` (requires docker
compose stack running on :8283).

Run:
    cd backend && pytest tests/test_letta_integration.py -v
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

# 让 `from app...` 能找到
_BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_BACKEND_ROOT))

# 强制 mock — 这些测试不依赖真实 LLM，只测 Letta client 协议层
os.environ.setdefault("USE_MOCK_LLM", "true")
# 关掉 Letta（不然走 graph.py 真实路径会撞 404）
os.environ.setdefault("USE_LETTA", "false")

import httpx  # noqa: E402
import pytest  # noqa: E402

from app.graph import ROLES  # noqa: E402
from app.letta_bridge import (  # noqa: E402
    LettaClient,
    agent_name_for,
    build_npc_memory_blocks,
)
from app.letta_bridge.agent_manager import (  # noqa: E402
    LettaAgentRegistry,
    ROLE_AGENT_KEYS,
    get_or_create_agent_id,
    set_default_registry,
)
from app.letta_bridge.letta_client import LettaClient as _LettaClientClass  # noqa: E402


# ============================================================================
# Mock transport — simulates Letta's REST + SSE responses
# ============================================================================
class MockLettaTransport(httpx.AsyncBaseTransport):
    """In-memory mock for the Letta REST API.

    Records every request so tests can assert on the payload shape, and
    serves canned responses for `/v1/agents`, `/v1/agents/{id}`,
    `/v1/agents/{id}/messages/stream` etc.

    This is enough to exercise the client + registry + bootstrap path
    without standing up a real Letta server.
    """

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.agents: dict[str, dict] = {}  # agent_id -> payload
        self.next_id = 1
        # Stream chunks (per agent) for /messages/stream — list of dicts.
        # When None, returns a default SSE body.
        self.stream_chunks_per_agent: dict[str, list[dict]] = {}

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path

        # GET /v1/health → 200 {version, status}
        if path.endswith("/v1/health"):
            return httpx.Response(
                200,
                json={"version": "0.16.8", "status": "ok"},
            )

        # GET /v1/agents → list of {id, name}
        if path.endswith("/v1/agents") and request.method == "GET":
            payload = [
                {"id": a["id"], "name": a["name"]}
                for a in self.agents.values()
            ]
            return httpx.Response(200, json=payload)

        # POST /v1/agents → create + return new agent
        if path.endswith("/v1/agents") and request.method == "POST":
            body = json.loads(request.content or b"{}")
            new_id = f"agent-mock-{self.next_id:04d}"
            self.next_id += 1
            self.agents[new_id] = {
                "id": new_id,
                "name": body.get("name", "unknown"),
                "model": body.get("model", ""),
                "memory_blocks": body.get("memory_blocks", []),
            }
            return httpx.Response(200, json={
                "id": new_id,
                "name": body.get("name", "unknown"),
                "agent_type": "letta_v1_agent",
                "memory": {"blocks": body.get("memory_blocks", [])},
            })

        # GET /v1/agents/{id}
        if path.startswith("/v1/agents/") and request.method == "GET":
            parts = path.split("/")
            agent_id = parts[3]
            if agent_id in self.agents:
                a = self.agents[agent_id]
                return httpx.Response(200, json={
                    "id": a["id"],
                    "name": a["name"],
                    "agent_type": "letta_v1_agent",
                    "memory": {"blocks": a.get("memory_blocks", [])},
                })
            return httpx.Response(404, json={"error": "agent not found"})

        # GET /v1/agents/{id}/core-memory → {blocks: [...]}
        if path.endswith("/core-memory") and request.method == "GET":
            parts = path.split("/")
            agent_id = parts[3]
            if agent_id in self.agents:
                a = self.agents[agent_id]
                return httpx.Response(200, json={
                    "blocks": a.get("memory_blocks", []),
                })
            return httpx.Response(404, json={"error": "not found"})

        # POST /v1/agents/{id}/archival-memory
        if path.endswith("/archival-memory") and request.method == "POST":
            json.loads(request.content or b"{}")
            # Echo a single passage id; trivial for our purposes.
            return httpx.Response(200, json={"result": "OK", "ids": [f"passage-{len(self.agents)}"]})

        # GET /v1/agents/{id}/archival-memory
        if path.endswith("/archival-memory") and request.method == "GET":
            return httpx.Response(200, json=[])

        # POST /v1/agents/{id}/messages/stream → SSE body
        if path.endswith("/messages/stream") and request.method == "POST":
            parts = path.split("/")
            agent_id = parts[3]
            chunks = self.stream_chunks_per_agent.get(agent_id)
            if chunks is None:
                # Default: 3 assistant_message chunks spelling "hi".
                chunks = [
                    {"message_type": "assistant_message", "content": "hi"},
                ]
            # Build SSE body: each event on its own blank-line-terminated
            # block with one `data:` line.  Real Letta emits multiple
            # events per turn; we collapse to one event per chunk for
            # simplicity.
            lines = []
            for ev in chunks:
                lines.append("data: " + json.dumps(ev))
                lines.append("")  # blank-line terminator
            lines.append("data: [DONE]")
            lines.append("")
            body = "\n".join(lines).encode("utf-8")
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content=body,
            )

        # Default: 404
        return httpx.Response(404, json={"error": f"unhandled: {request.method} {path}"})


@pytest.fixture
def mock_letta_transport(monkeypatch):
    """Install a mock Letta transport before any client is constructed.

    The mock is auto-cleared after each test (monkeypatch fixture).
    """
    transport = MockLettaTransport()
    _LettaClientClass.set_test_transport(transport)
    # Reset singleton so it picks up the mock
    from app.letta_bridge import set_letta_client_for_tests
    set_letta_client_for_tests(None)
    yield transport
    _LettaClientClass.set_test_transport(None)
    set_letta_client_for_tests(None)


@pytest.fixture
def fresh_registry(monkeypatch):
    """An in-memory registry, registered as the process singleton."""
    reg = LettaAgentRegistry(":memory:")
    set_default_registry(reg)
    yield reg
    set_default_registry(None)
    reg.close()


# ============================================================================
# Test 1: LettaClient.health() round-trip
# ============================================================================
@pytest.mark.asyncio
async def test_letta_client_health_via_mock(mock_letta_transport):
    """GET /v1/health returns 200 JSON via the mock transport."""
    client = LettaClient(base_url="http://letta-mock:8283", password="letta_dev_password")
    try:
        info = await client.health()
        assert info["version"] == "0.16.8"
        assert info["status"] == "ok"
        # Assert the request was actually made
        assert any("/v1/health" in r.url.path for r in mock_letta_transport.requests)
    finally:
        await client.aclose()


# ============================================================================
# Test 2: LettaClient.create_agent sends correct memory_blocks payload
# ============================================================================
@pytest.mark.asyncio
async def test_letta_create_agent_payload_shape(mock_letta_transport):
    """POST /v1/agents carries memory_blocks derived from ROLES[key].system."""
    client = LettaClient(base_url="http://letta-mock:8283", password="letta_dev_password")
    try:
        role_key = "bai-qianbei"
        role = ROLES[role_key]
        blocks = build_npc_memory_blocks(role_key)

        # Sanity: 4 blocks (persona / human / preferences / relationships)
        assert len(blocks) == 4
        labels = [b["label"] for b in blocks]
        assert labels == ["persona", "human", "preferences", "relationships"]

        # persona block contains the role's full system prompt
        # 2026-07-04: with the IDENTITY ANCHOR wrap, the persona block now
        # starts with 【身份锚】 prefix. Compare against _wrap_persona output.
        from app.graph import _wrap_persona
        persona_block = next(b for b in blocks if b["label"] == "persona")
        assert persona_block["value"] == _wrap_persona(role_key, role["system"])

        # create_agent hits the mock
        created = await client.create_agent({
            "name": agent_name_for(role_key),
            "model": "openai/gpt-4o-mini",
            "memory_blocks": blocks,
        })
        assert created["id"].startswith("agent-mock-")
        assert created["name"] == "npc-bai-qianbei"

        # POST body captured by mock — verify the persona text was sent
        post_reqs = [r for r in mock_letta_transport.requests if r.method == "POST" and r.url.path.endswith("/v1/agents")]
        assert len(post_reqs) == 1
        sent_body = json.loads(post_reqs[0].content)
        sent_persona = next(b for b in sent_body["memory_blocks"] if b["label"] == "persona")
        assert sent_persona["value"] == _wrap_persona(role_key, role["system"])
    finally:
        await client.aclose()


# ============================================================================
# Test 3: LettaAgentRegistry.bootstrap_all creates 6 NPCs from empty state
# ============================================================================
@pytest.mark.asyncio
async def test_letta_registry_bootstrap_creates_six(mock_letta_transport, fresh_registry):
    """On first bootstrap, exactly 6 NPCs (one per ROLES key) are created."""
    client = LettaClient(base_url="http://letta-mock:8283", password="letta_dev_password")
    try:
        outcome = await fresh_registry.bootstrap_all(
            client=client, llm_model="openai/gpt-4o-mini",
        )
        # 6 created, 0 reused, 0 recovered, 0 failed
        assert sorted(outcome.created) == sorted(ROLE_AGENT_KEYS)
        assert outcome.reused == []
        assert outcome.recovered == []
        assert outcome.failed == []

        # Registry has 6 rows
        rows = fresh_registry.list_all()
        assert len(rows) == 6
        assert sorted(r["role_key"] for r in rows) == sorted(ROLE_AGENT_KEYS)
        assert all(r["agent_id"].startswith("agent-mock-") for r in rows)
        assert all(r["agent_name"] == f"npc-{r['role_key']}" for r in rows)

        # Mock served 6 create_agent POSTs
        create_reqs = [r for r in mock_letta_transport.requests if r.method == "POST" and r.url.path.endswith("/v1/agents")]
        assert len(create_reqs) == 6
    finally:
        await client.aclose()


# ============================================================================
# Test 4: bootstrap_all is idempotent — second run reports all 6 as reused
# ============================================================================
@pytest.mark.asyncio
async def test_letta_registry_bootstrap_idempotent(mock_letta_transport, fresh_registry):
    """Re-running bootstrap_all reuses the existing agents (no new POSTs)."""
    client = LettaClient(base_url="http://letta-mock:8283", password="letta_dev_password")
    try:
        # First bootstrap → 6 created
        out1 = await fresh_registry.bootstrap_all(
            client=client, llm_model="openai/gpt-4o-mini",
        )
        assert len(out1.created) == 6
        created_post_count_1 = sum(
            1 for r in mock_letta_transport.requests
            if r.method == "POST" and r.url.path.endswith("/v1/agents")
        )
        assert created_post_count_1 == 6

        # Second bootstrap → all 6 reused, no new POSTs
        out2 = await fresh_registry.bootstrap_all(
            client=client, llm_model="openai/gpt-4o-mini",
        )
        assert out2.created == []
        assert sorted(out2.reused) == sorted(ROLE_AGENT_KEYS)
        assert out2.recovered == []
        assert out2.failed == []

        # No additional create_agent POSTs
        created_post_count_2 = sum(
            1 for r in mock_letta_transport.requests
            if r.method == "POST" and r.url.path.endswith("/v1/agents")
        )
        assert created_post_count_2 == created_post_count_1, (
            f"expected no new POSTs on 2nd bootstrap, got {created_post_count_2 - created_post_count_1} extra"
        )
    finally:
        await client.aclose()


# ============================================================================
# Test 5: get_or_create_agent_id hot-path: cached lookup is consistent
# ============================================================================
@pytest.mark.asyncio
async def test_letta_get_or_create_agent_id_consistency(mock_letta_transport, fresh_registry):
    """After bootstrap, get_or_create_agent_id returns the SAME agent_id
    on repeated calls without creating new agents.
    """
    client = LettaClient(base_url="http://letta-mock:8283", password="letta_dev_password")
    try:
        # Pre-populate registry by bootstrapping
        await fresh_registry.bootstrap_all(
            client=client, llm_model="openai/gpt-4o-mini",
        )
        post_count_before = sum(
            1 for r in mock_letta_transport.requests
            if r.method == "POST" and r.url.path.endswith("/v1/agents")
        )

        # Hot-path: lookup 10x per role (60 lookups total)
        # Each call must verify the agent is alive (GET) — no new POSTs.
        seen_ids: dict[str, str] = {}
        for role_key in ROLE_AGENT_KEYS:
            for _ in range(10):
                aid = await get_or_create_agent_id(
                    client=client,
                    role_key=role_key,
                    llm_model="openai/gpt-4o-mini",
                    registry=fresh_registry,
                )
                if role_key in seen_ids:
                    assert aid == seen_ids[role_key], (
                        f"{role_key} returned inconsistent agent_id: "
                        f"{aid} vs {seen_ids[role_key]}"
                    )
                else:
                    seen_ids[role_key] = aid

        # 60 lookups → still only the original 6 create POSTs.
        post_count_after = sum(
            1 for r in mock_letta_transport.requests
            if r.method == "POST" and r.url.path.endswith("/v1/agents")
        )
        assert post_count_after == post_count_before, (
            f"hot-path leaked {post_count_after - post_count_before} extra POSTs"
        )

        # All 6 unique agent_ids
        assert len(set(seen_ids.values())) == 6
    finally:
        await client.aclose()


# ============================================================================
# Test 6 (bonus): _stream_via_letta decodes SSE into content pieces
# ============================================================================
@pytest.mark.asyncio
async def test_letta_stream_message_decodes_sse(mock_letta_transport, fresh_registry, monkeypatch):
    """Letta's SSE stream events with `message_type=assistant_message` and
    `content` get yielded as plain text pieces by `_stream_via_letta`.
    """
    # Force graph.py to take the Letta path
    monkeypatch.setenv("USE_LETTA", "true")
    monkeypatch.setenv("USE_MOCK_LLM", "false")
    from app.config import get_settings
    get_settings.cache_clear()  # noqa: SLF001 — invalidate lru_cache

    # Pre-create the agent for shu-hang so the registry knows about it
    client = LettaClient(base_url="http://letta-mock:8283", password="letta_dev_password")
    try:
        # Bootstrap so agent_id exists
        await fresh_registry.bootstrap_all(
            client=client, llm_model="openai/gpt-4o-mini",
        )
        # Configure mock to return a multi-chunk response for shu-hang
        shu_id = fresh_registry.get_agent_id("shu-hang")
        mock_letta_transport.stream_chunks_per_agent[shu_id] = [
            {"message_type": "assistant_message", "content": "妈耶"},
            {"message_type": "assistant_message", "content": "，大家好"},
            {"message_type": "reasoning_message", "content": "（思考过程）"},
            {"message_type": "assistant_message", "content": "！在下宋书航！"},
        ]

        # Configure graph.py to use the singleton client (it does, by default)
        from app.letta_bridge import set_letta_client_for_tests
        set_letta_client_for_tests(client)

        # Call _stream_via_letta directly
        from app.graph import _stream_via_letta
        from langchain_core.messages import HumanMessage, SystemMessage

        msgs = [
            SystemMessage(content=ROLES["shu-hang"]["system"]),
            HumanMessage(content="大家好"),
        ]
        pieces: list[str] = []
        async for piece in _stream_via_letta(
            role_key="shu-hang",
            session_id="test-sess",
            all_msgs=msgs,
        ):
            pieces.append(piece)

        # Filter out reasoning_message content — _stream_via_letta should
        # only yield assistant_message chunks.
        joined = "".join(pieces)
        assert "妈耶" in joined
        assert "在下宋书航" in joined
        # Reasoning content should NOT be yielded (we filter by message_type).
        assert "思考过程" not in joined, "reasoning_message content leaked into reply"
    finally:
        await client.aclose()
        # Reset settings cache so other tests see USE_LETTA=false again
        from app.config import get_settings
        get_settings.cache_clear()  # noqa: SLF001


# ============================================================================
# Standalone runner
# ============================================================================
async def _run_async_tests() -> int:
    """Drive the async tests sequentially (no pytest needed)."""
    failures = 0
    transport = MockLettaTransport()
    _LettaClientClass.set_test_transport(transport)
    try:
        from app.letta_bridge import set_letta_client_for_tests
        set_letta_client_for_tests(None)

        for name, fn in [
            ("test_letta_client_health_via_mock", test_letta_client_health_via_mock),
            ("test_letta_create_agent_payload_shape", test_letta_create_agent_payload_shape),
            ("test_letta_registry_bootstrap_creates_six", test_letta_registry_bootstrap_creates_six),
            ("test_letta_registry_bootstrap_idempotent", test_letta_registry_bootstrap_idempotent),
            ("test_letta_get_or_create_agent_id_consistency", test_letta_get_or_create_agent_id_consistency),
            ("test_letta_stream_message_decodes_sse", test_letta_stream_message_decodes_sse),
        ]:
            # Build a fresh registry each test
            reg = LettaAgentRegistry(":memory:")
            set_default_registry(reg)
            try:
                # Wrap fixture-style call manually (monkeypatch not available)
                import inspect
                sig = inspect.signature(fn)
                kwargs = {}
                if "mock_letta_transport" in sig.parameters:
                    kwargs["mock_letta_transport"] = transport
                if "fresh_registry" in sig.parameters:
                    kwargs["fresh_registry"] = reg
                await fn(**kwargs)
                print(f"  [async] {name}: PASS")
            except Exception as e:
                failures += 1
                print(f"  [async] {name}: FAIL — {e}")
            finally:
                set_default_registry(None)
                reg.close()
    finally:
        _LettaClientClass.set_test_transport(None)
    return failures


if __name__ == "__main__":
    rc = asyncio.run(_run_async_tests())
    print(f"\nLetta integration async failures: {rc}")
    sys.exit(rc)