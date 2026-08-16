"""Tests for GET /api/group/history endpoint.

T6 spec — group chat history persistence (WeChat-like reload behavior):
- Empty session → empty history
- With messages → chronological ASC order
- limit honored (default 100, max 500)
- Cross-session isolation (sid_a cannot read sid_b's events)
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.memory.agent_memory import (
    AgentMemoryStore,
    set_agent_memory_store,
    close_agent_memory_store,
)


@pytest.fixture
def store(tmp_path):
    """Per-test fresh AgentMemoryStore on tmp DB."""
    db = tmp_path / "test_agent_memory.sqlite"
    s = AgentMemoryStore(db_path=db)
    set_agent_memory_store(s)
    yield s
    close_agent_memory_store()
    set_agent_memory_store(None)


@pytest.fixture
def client(store):
    return TestClient(app)


def _seed_group_events(store: AgentMemoryStore, session_id: str, count: int, prefix: str | None = None) -> None:
    """Insert `count` group fan-out events with monotonic timestamps.

    `prefix` (default: session_id) is used in the text so cross-session
    isolation tests can verify no leakage by comparing disjoint text sets.
    """
    import time
    base_ts = int(time.time() * 1000) - count * 1000  # past timestamps
    text_tag = prefix if prefix is not None else session_id
    for i in range(count):
        store.fan_out_group_event(
            session_id=session_id,
            speaker_key="user" if i % 2 == 0 else "shu-hang",
            text=f"{text_tag}-msg-{i}",
            timestamp=base_ts + i * 100,
            agent_name=None if i % 2 == 0 else "宋书航",
            agent_emoji=None if i % 2 == 0 else "🦊",
        )


# ---------------------------------------------------------------------------
# Empty + happy path
# ---------------------------------------------------------------------------

def test_empty_session_returns_empty_list(client):
    """session with no events returns 200 + history=[] + count=0."""
    r = client.get("/api/group/history", params={"session_id": "sid-empty"})
    assert r.status_code == 200
    body = r.json()
    assert body["session_id"] == "sid-empty"
    assert body["count"] == 0
    assert body["limit"] == 100  # default
    assert body["history"] == []


def test_with_messages_returns_in_chronological_asc_order(client, store):
    """Events come back in ASC timestamp order (oldest first)."""
    _seed_group_events(store, "sid-A", count=5, prefix="A")
    r = client.get("/api/group/history", params={"session_id": "sid-A"})
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 5
    history = body["history"]
    texts = [h["text"] for h in history]
    assert texts == [f"A-msg-{i}" for i in range(5)]
    # timestamps strictly increasing
    ts = [h["timestamp"] for h in history]
    assert ts == sorted(ts)


def test_event_shape_matches_spec(client, store):
    """Each event has the documented fields."""
    _seed_group_events(store, "sid-shape", count=1, prefix="S")
    r = client.get("/api/group/history", params={"session_id": "sid-shape"})
    ev = r.json()["history"][0]
    expected_keys = {
        "agent_key", "role", "source", "speaker_key", "text",
        "timestamp", "agent_name", "agent_emoji",
        # T9 / Piece B: human author (user-typed entries get '神秘人' fallback;
        # AI-typed entries get None).
        "author",
    }
    assert set(ev.keys()) >= expected_keys
    assert ev["source"] == "group"
    assert ev["role"] in ("user", "agent")
    assert ev["text"] == "S-msg-0"
    # user-typed row (i % 2 == 0) gets author backfilled; AI-typed gets None
    if ev["role"] == "user":
        assert ev["author"] == "神秘人"
    else:
        assert ev["author"] is None


# ---------------------------------------------------------------------------
# limit honored
# ---------------------------------------------------------------------------

def test_default_limit_is_100(client, store):
    """With 150 events + default limit, response contains 100 (the most recent)."""
    _seed_group_events(store, "sid-many", count=150, prefix="M")
    r = client.get("/api/group/history", params={"session_id": "sid-many"})
    body = r.json()
    assert body["limit"] == 100
    assert body["count"] == 100
    # The 100 returned should be the LAST 100 (most recent) in ASC order
    history = body["history"]
    assert history[0]["text"] == "M-msg-50"  # 150 - 100 = 50
    assert history[-1]["text"] == "M-msg-149"


def test_explicit_limit_honored(client, store):
    """limit=10 returns last 10 events."""
    _seed_group_events(store, "sid-limit", count=20, prefix="L")
    r = client.get(
        "/api/group/history",
        params={"session_id": "sid-limit", "limit": 10},
    )
    body = r.json()
    assert body["count"] == 10
    assert body["limit"] == 10
    texts = [h["text"] for h in body["history"]]
    assert texts == [f"L-msg-{i}" for i in range(10, 20)]


def test_limit_above_max_rejected(client):
    """limit=501 is rejected by FastAPI Query(le=500)."""
    r = client.get(
        "/api/group/history",
        params={"session_id": "any", "limit": 501},
    )
    assert r.status_code == 422


def test_limit_zero_rejected(client):
    """limit=0 is rejected (ge=1)."""
    r = client.get(
        "/api/group/history",
        params={"session_id": "any", "limit": 0},
    )
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# Cross-session isolation (Stage 6 DM Phase 2 invariant: privacy guarantee)
# ---------------------------------------------------------------------------

def test_cross_session_isolation(client, store):
    """session A events must not leak to session B query."""
    _seed_group_events(store, "sid-A", count=3, prefix="A")
    _seed_group_events(store, "sid-B", count=5, prefix="B")

    r_a = client.get("/api/group/history", params={"session_id": "sid-A"})
    r_b = client.get("/api/group/history", params={"session_id": "sid-B"})

    assert r_a.json()["count"] == 3
    assert r_b.json()["count"] == 5
    # Verify no overlap (unique prefixes make text disjoint by construction)
    texts_a = {h["text"] for h in r_a.json()["history"]}
    texts_b = {h["text"] for h in r_b.json()["history"]}
    assert texts_a.isdisjoint(texts_b)
    # Every A text should start with the A prefix
    assert all(t.startswith("A-msg-") for t in texts_a)
    assert all(t.startswith("B-msg-") for t in texts_b)


def test_empty_session_id_rejected(client):
    """session_id='' (or missing) is rejected at FastAPI validation layer."""
    r1 = client.get("/api/group/history", params={"session_id": ""})
    assert r1.status_code == 422
    r2 = client.get("/api/group/history")
    assert r2.status_code == 422


# ---------------------------------------------------------------------------
# DM-source events must NOT bleed into group history
# ---------------------------------------------------------------------------

def test_dm_source_events_excluded(client, store):
    """DM messages from this session (stored on dm target agent, not fan-out
    to shu-hang) must not appear in /api/group/history (which reads shu-hang
    and filters to source='group')."""
    # Seed group events to shu-hang's memory
    _seed_group_events(store, "sid-mix", count=2)
    # Seed a DM event on a DIFFERENT agent (ling-die), NOT fanned out
    store.append_message(
        session_id="sid-mix",
        agent_key="ling-die",
        role="user",
        source="dm",
        speaker_key="user",
        text="private dm message",
    )
    r = client.get("/api/group/history", params={"session_id": "sid-mix"})
    body = r.json()
    # Only the 2 group events should appear, not the DM
    assert body["count"] == 2
    texts = [h["text"] for h in body["history"]]
    assert "private dm message" not in texts