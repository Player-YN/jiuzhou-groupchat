"""Tests for DELETE /api/group/history and DELETE /api/dm/history endpoints.

T9 / Piece C: Clear button配套.
覆盖:
- DELETE /api/group/history: 只删 source='group', 保留 dm 行
- DELETE /api/dm/history (no agent_key): 清空该 session 下所有 DM 行
- DELETE /api/dm/history (specific agent_key): 只清该 target 的 DM 行
- Cross-session isolation: A session 的 delete 不影响 B session
- Empty session: DELETE 返 {deleted: 0}
- Invalid session_id: 422 (FastAPI validation)
- delete_session() 在 agent_memory.py 的单元行为 (basic + cross-source)
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
    db = tmp_path / "test_history_delete.sqlite"
    s = AgentMemoryStore(db_path=db)
    set_agent_memory_store(s)
    yield s
    close_agent_memory_store()
    set_agent_memory_store(None)


@pytest.fixture
def client(store):
    return TestClient(app)


def _seed_group_events(store: AgentMemoryStore, session_id: str, count: int, prefix: str | None = None) -> None:
    """Insert `count` group fan-out events with monotonic timestamps."""
    import time
    base_ts = int(time.time() * 1000) - count * 1000
    text_tag = prefix if prefix is not None else session_id
    for i in range(count):
        store.fan_out_group_event(
            session_id=session_id,
            speaker_key="user" if i % 2 == 0 else "shu-hang",
            text=f"{text_tag}-msg-{i}",
            timestamp=base_ts + i * 100,
            agent_name=None if i % 2 == 0 else "宋书航",
            agent_emoji=None if i % 2 == 0 else "🌟",
        )


def _seed_dm_events(store: AgentMemoryStore, session_id: str, agent_key: str, count: int) -> None:
    """Insert `count` DM messages for one (session_id, agent_key) pair."""
    import time
    base_ts = int(time.time() * 1000) - count * 1000
    for i in range(count):
        store.append_message(
            session_id=session_id,
            agent_key=agent_key,
            role="user" if i % 2 == 0 else "agent",
            source="dm",
            speaker_key="user" if i % 2 == 0 else agent_key,
            text=f"{agent_key}-dm-{i}",
            timestamp=base_ts + i * 100,
        )


# ============================================================================
# delete_session 单元行为 (AgentMemoryStore method 直接测)
# ============================================================================

def test_delete_session_by_session_only_clears_everything(store):
    """delete_session(session_id=X) 清空该 session 全部 (group + dm × 所有 agent)."""
    _seed_group_events(store, "sess-X", count=3)
    _seed_dm_events(store, "sess-X", "shu-hang", count=2)
    _seed_dm_events(store, "sess-X", "yao-shi", count=2)
    # 全部 6 角色都有 group 行 (fan-out), 加 4 个 dm 行
    assert store.count_messages("sess-X", "shu-hang") == 3 + 2  # 3 group + 2 dm
    assert store.count_messages("sess-X", "yao-shi") == 3 + 2

    deleted = store.delete_session(session_id="sess-X")
    # 6 角色 × 3 group + 2 个 dm = 20 行
    assert deleted == 18 + 4  # 6 * 3 + 2 + 2 = 22
    assert store.count_messages("sess-X", "shu-hang") == 0
    assert store.count_messages("sess-X", "yao-shi") == 0


def test_delete_session_with_source_group_only(store):
    """delete_session(session_id=X, source='group') 只清 group 行, 保留 dm."""
    _seed_group_events(store, "sess-X", count=3)  # 6×3 = 18 group rows
    _seed_dm_events(store, "sess-X", "shu-hang", count=2)

    deleted = store.delete_session(session_id="sess-X", source="group")
    assert deleted == 18  # 6 × 3
    # dm 行还在
    assert store.count_messages("sess-X", "shu-hang") == 2  # 0 group + 2 dm
    # 其他角色 group 清空
    assert store.count_messages("sess-X", "yao-shi") == 0


def test_delete_session_with_agent_key_only(store):
    """delete_session(session_id=X, agent_key='shu-hang') 只清该 agent 行."""
    _seed_dm_events(store, "sess-X", "shu-hang", count=3)
    _seed_dm_events(store, "sess-X", "yao-shi", count=2)

    deleted = store.delete_session(session_id="sess-X", agent_key="shu-hang")
    assert deleted == 3
    assert store.count_messages("sess-X", "shu-hang") == 0
    # yao-shi 行还在
    assert store.count_messages("sess-X", "yao-shi") == 2


def test_delete_session_with_agent_and_source_dm(store):
    """delete_session(session_id=X, agent_key='shu-hang', source='dm') 精确组合."""
    _seed_group_events(store, "sess-X", count=2)  # fan-out 6×2 = 12 group rows total (2 per agent)
    _seed_dm_events(store, "sess-X", "shu-hang", count=3)

    deleted = store.delete_session(
        session_id="sess-X", agent_key="shu-hang", source="dm",
    )
    assert deleted == 3
    # 每个 agent 有 2 group 行 (fan-out)
    # shu-hang: 2 group + 3 dm - 3 dm = 2 (group 行留着)
    assert store.count_messages("sess-X", "shu-hang") == 2
    assert store.count_messages("sess-X", "yao-shi") == 2
    assert store.count_messages("sess-X", "ling-die") == 2


def test_delete_session_invalid_source_raises(store):
    """source 参数必须是 None / 'group' / 'dm', 其他报错."""
    with pytest.raises(ValueError):
        store.delete_session(session_id="sess-X", source="bogus")


def test_delete_session_empty_session_returns_zero(store):
    """不存在的 session_id → 返 0, 不抛错."""
    assert store.delete_session(session_id="never-existed") == 0
    assert store.delete_session(session_id="never-existed", source="group") == 0


def test_delete_session_empty_string_returns_zero(store):
    """空字符串 session_id → 返 0 (defensive)."""
    assert store.delete_session(session_id="") == 0


def test_delete_session_cross_session_isolation(store):
    """删 sess-A 不影响 sess-B."""
    _seed_group_events(store, "sess-A", count=2)
    _seed_group_events(store, "sess-B", count=5)

    deleted = store.delete_session(session_id="sess-A")
    assert deleted == 12  # 6 × 2
    # sess-A 全清
    assert store.count_messages("sess-A", "shu-hang") == 0
    # sess-B 不动
    assert store.count_messages("sess-B", "shu-hang") == 5


# ============================================================================
# HTTP endpoint 集成测试 (TestClient)
# ============================================================================

def test_http_delete_group_history_clears_group_only(client, store):
    """DELETE /api/group/history 删 group 行, 保留 dm 行."""
    _seed_group_events(store, "sess-X", count=2)  # 6×2 = 12 group
    _seed_dm_events(store, "sess-X", "shu-hang", count=3)

    r = client.delete("/api/group/history", params={"session_id": "sess-X"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["deleted"] == 12
    assert body["session_id"] == "sess-X"
    assert body["scope"] == "group"

    # group 清空, dm 还在
    assert store.count_messages("sess-X", "shu-hang") == 3  # dm 行
    assert store.count_messages("sess-X", "yao-shi") == 0  # group 行被清


def test_http_delete_group_history_empty_session_returns_zero(client):
    """空 session DELETE 返 deleted=0 + 200."""
    r = client.delete("/api/group/history", params={"session_id": "never-existed"})
    assert r.status_code == 200
    assert r.json()["deleted"] == 0


def test_http_delete_group_history_empty_session_id_422(client):
    """空字符串 session_id → FastAPI 422."""
    r = client.delete("/api/group/history", params={"session_id": ""})
    assert r.status_code == 422
    r2 = client.delete("/api/group/history")
    assert r2.status_code == 422


def test_http_delete_dm_history_no_agent_key_clears_all_targets(client, store):
    """DELETE /api/dm/history (no agent_key) 清空该 session 所有 DM target."""
    _seed_dm_events(store, "sess-X", "shu-hang", count=2)
    _seed_dm_events(store, "sess-X", "yao-shi", count=3)
    # group 行不被删
    _seed_group_events(store, "sess-X", count=1)  # 6×1 = 6 group

    r = client.delete("/api/dm/history", params={"session_id": "sess-X"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["deleted"] == 5
    assert body["session_id"] == "sess-X"
    assert body["agent_key"] is None
    assert body["scope"] == "dm"

    # 所有 DM 清空
    assert store.count_messages("sess-X", "shu-hang") == 1  # 只剩 group 行
    assert store.count_messages("sess-X", "yao-shi") == 1
    # 其他 agent 也只剩 group 行
    assert store.count_messages("sess-X", "bei-he") == 1


def test_http_delete_dm_history_specific_agent_key(client, store):
    """DELETE /api/dm/history?agent_key=shu-hang 只清 shu-hang 的 DM."""
    _seed_dm_events(store, "sess-X", "shu-hang", count=3)
    _seed_dm_events(store, "sess-X", "yao-shi", count=2)

    r = client.delete(
        "/api/dm/history",
        params={"session_id": "sess-X", "agent_key": "shu-hang"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["deleted"] == 3
    assert body["agent_key"] == "shu-hang"

    # shu-hang DM 清空, yao-shi 不动
    assert store.count_messages("sess-X", "shu-hang") == 0
    assert store.count_messages("sess-X", "yao-shi") == 2


def test_http_delete_dm_history_group_intact(client, store):
    """DELETE /api/dm/history 不影响 group 行."""
    _seed_group_events(store, "sess-X", count=2)  # 6×2 = 12 group
    _seed_dm_events(store, "sess-X", "shu-hang", count=2)

    r = client.delete("/api/dm/history", params={"session_id": "sess-X"})
    assert r.status_code == 200
    assert r.json()["deleted"] == 2

    # group 行全留 (6 角色都还在)
    for k in ("shu-hang", "yao-shi", "san-lang", "bei-he", "bai-qianbei", "ling-die"):
        assert store.count_messages("sess-X", k) == 2, f"{k} 应该有 2 group 行"


def test_http_delete_dm_history_cross_session_isolation(client, store):
    """DELETE /api/dm/history?sid=A 不影响 sid=B 的 DM."""
    _seed_dm_events(store, "sess-A", "shu-hang", count=3)
    _seed_dm_events(store, "sess-B", "shu-hang", count=5)

    r = client.delete("/api/dm/history", params={"session_id": "sess-A"})
    assert r.json()["deleted"] == 3

    # sess-B 完全不动
    assert store.count_messages("sess-B", "shu-hang") == 5
    # sess-A 清空
    assert store.count_messages("sess-A", "shu-hang") == 0


def test_http_delete_dm_history_empty_session_id_422(client):
    """空字符串 session_id → 422."""
    r = client.delete("/api/dm/history", params={"session_id": ""})
    assert r.status_code == 422


def test_http_delete_dm_history_empty_agent_key_treated_as_no_filter(client, store):
    """空字符串 agent_key 视同 None (清所有)."""
    _seed_dm_events(store, "sess-X", "shu-hang", count=2)
    _seed_dm_events(store, "sess-X", "yao-shi", count=2)

    r = client.delete(
        "/api/dm/history",
        params={"session_id": "sess-X", "agent_key": "   "},  # 全空格
    )
    assert r.status_code == 200
    # 服务端 trim 后视同 None → 清所有 target
    assert r.json()["deleted"] == 4
    assert r.json()["agent_key"] is None


# ============================================================================
# OpenAPI schema 检查 (确认 DELETE 端点真的注册)
# ============================================================================

def test_openapi_includes_delete_endpoints():
    """两个 DELETE 端点必须出现在 OpenAPI schema (避免 register 漏写)."""
    spec = app.openapi()
    paths = spec.get("paths", {})
    assert "/api/group/history" in paths
    assert "/api/dm/history" in paths
    assert "delete" in paths["/api/group/history"]
    assert "delete" in paths["/api/dm/history"]