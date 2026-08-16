"""AgentMemoryStore unit tests — Stage 7 Bug 2 per-agent unified memory.

覆盖 `.harness/reports/agent_memory_design.md` 验收点：
1. basic_crud — append / load / count / clear
2. isolation — per-(session_id, agent_key) 隔离
3. source_aware — source 字段正确,dm 不会混进 group
4. fan_out — fan_out_group_event 写 6 份,所有 6 角色 load 都能拿到
5. privacy_dm_isolation — Y dm with user -> X.load_agent_memory 拿不到
6. validation — role/source/text/session/agent/speaker 校验
7. list_sessions — 跨 session 列表
8. agent_name_emoji — 持久化还原正确

测试模式：
- pytest 风格 `def test_X()` 函数（让 `pytest tests/` 真正收集到）
- `if __name__ == "__main__"` 入口也支持 standalone 运行（与其他 stage 测试一致）
- 用 `:memory:` SQLite store 隔离，patch `get_chat_model` 走 MockChatModel（如有 LLM 依赖）

跑法：
- pytest:  `cd backend && pytest tests/test_agent_memory.py -v`
- 独立:    `cd backend && python tests/test_agent_memory.py`
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# 把 backend 加到 path，这样 `from app...` 能找到
_BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_BACKEND_ROOT))

# 强制 mock LLM（避免触及真实 provider） — 本测试不调 LLM，但保底
os.environ.setdefault("USE_MOCK_LLM", "true")

import pytest  # noqa: E402

from app.memory.agent_memory import (  # noqa: E402
    AgentMemoryStore,
    ROLE_AGENT_KEYS,
    set_agent_memory_store,
)


# ============================================================================
# Fixtures
# ============================================================================
@pytest.fixture
def temp_store(monkeypatch):
    """每个测试一个独立的 SQLite store（:memory:），注入到全局默认。

    用 `:memory:` 保证测试之间零状态泄漏。
    """
    store = AgentMemoryStore(":memory:")
    set_agent_memory_store(store)
    yield store
    set_agent_memory_store(None)  # 还原
    store.close()


# ============================================================================
# Test 1: Basic CRUD
# ============================================================================
def test_agent_memory_basic_crud():
    """基本 CRUD: append + load + count + clear。"""
    s = AgentMemoryStore(":memory:")
    assert s.count_messages("s1", "shu-hang") == 0
    assert s.load_agent_memory("s1", "shu-hang") == []

    # append 一条
    e = s.append_message(
        session_id="s1",
        agent_key="shu-hang",
        role="user",
        source="dm",
        speaker_key="user",
        text="你好书航",
        timestamp=1000,
    )
    assert e.role == "user"
    assert e.source == "dm"
    assert e.speaker_key == "user"
    assert e.text == "你好书航"
    assert e.timestamp == 1000
    assert e.agent_key == "shu-hang"
    # T9 / Piece B: user-typed 行的 author 默认 fallback '神秘人'
    assert e.author == "神秘人"

    # load 回来
    hist = s.load_agent_memory("s1", "shu-hang")
    assert len(hist) == 1
    assert hist[0].text == "你好书航"
    assert hist[0].author == "神秘人"  # 持久化 + 读回都生效

    # count
    assert s.count_messages("s1", "shu-hang") == 1

    # append 第二条 (agent 发言，author 应该为 None)
    s.append_message(
        session_id="s1",
        agent_key="shu-hang",
        role="agent",
        source="dm",
        speaker_key="shu-hang",
        text="哈哈",
        timestamp=2000,
        agent_name="宋书航",
        agent_emoji="🌟",
    )
    hist = s.load_agent_memory("s1", "shu-hang")
    assert len(hist) == 2
    assert hist[0].text == "你好书航"
    assert hist[0].author == "神秘人"
    assert hist[1].text == "哈哈"
    assert hist[1].agent_name == "宋书航"
    assert hist[1].agent_emoji == "🌟"
    assert hist[1].author is None  # agent-typed 行 author=None


# ============================================================================
# T9 / Piece B: author field — explicit kwarg + 命名 author
# ============================================================================
def test_agent_memory_author_kwarg_explicit():
    """显式传 author 时, 应该按值持久化 (不被 fallback 覆盖)。"""
    s = AgentMemoryStore(":memory:")
    e = s.append_message(
        session_id="sess-a",
        agent_key="shu-hang",
        role="user",
        source="dm",
        speaker_key="user",
        text="hi",
        author="少年A",
    )
    assert e.author == "少年A"
    hist = s.load_agent_memory("sess-a", "shu-hang")
    assert hist[0].author == "少年A"


def test_agent_memory_author_empty_string_falls_back():
    """显式传空字符串 author 时, 应该 fallback 到 '神秘人'。"""
    s = AgentMemoryStore(":memory:")
    e = s.append_message(
        session_id="sess-a",
        agent_key="shu-hang",
        role="user",
        source="dm",
        speaker_key="user",
        text="hi",
        author="   ",  # 全空格
    )
    assert e.author == "神秘人"


def test_agent_memory_fan_out_author_propagates():
    """fan_out_group_event 的 author 透传到所有 audience row。"""
    s = AgentMemoryStore(":memory:")
    entries = s.fan_out_group_event(
        session_id="sess-fan",
        speaker_key="user",
        role="user",
        text="hello all",
        author="匿名散修",
    )
    assert len(entries) == 6  # 全 6 角色
    for e in entries:
        assert e.author == "匿名散修"
    # 读回每个角色的 memory 验证持久化
    for k in ("shu-hang", "yao-shi", "san-lang", "bei-he", "bai-qianbei", "ling-die"):
        h = s.load_agent_memory("sess-fan", k)
        assert len(h) == 1
        assert h[0].author == "匿名散修"


def test_agent_memory_author_column_migration_idempotent():
    """init_schema 应当幂等运行,不破坏已迁移过的 DB。

    Smoke 测: 创建一个 store, init 两次 (实际上 __init__ 已经 init 过);
    加几条 row; 然后 close + 重新 open + init, 应该全部 row 还在 + author
    列也还在。
    """
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False) as tf:
        path = tf.name

    # 1) 第一次 open + init
    s1 = AgentMemoryStore(path)
    s1.append_message(
        "sess-1", "shu-hang", "user", "dm", "user", "hi", author="少年A",
    )
    s1.close()

    # 2) 第二次 open (模拟 server restart): init_schema 跑 ALTER 检查
    s2 = AgentMemoryStore(path)
    # author 列应该还在, row 应该还在
    h = s2.load_agent_memory("sess-1", "shu-hang")
    assert len(h) == 1
    assert h[0].author == "少年A"
    s2.close()

# cleanup
    import os
    if os.path.exists(path):
        os.unlink(path)


# ============================================================================
# Test 2: Isolation
# ============================================================================
def test_agent_memory_isolation():
    """核心隔离：per-(session_id, agent_key) 隔离。

    即使调用方传错 agent_key，也只会返回空列表（SQL 严格 WHERE 过滤）。
    """
    s = AgentMemoryStore(":memory:")

    # sess1/shu-hang 写 2 条
    s.append_message("sess1", "shu-hang", "user", "dm", "user", "你好书航", timestamp=1000)
    s.append_message(
        "sess1", "shu-hang", "agent", "dm", "shu-hang", "哈哈",
        timestamp=2000, agent_name="宋书航", agent_emoji="🌟",
    )

    # sess1/yao-shi 写 1 条
    s.append_message("sess1", "yao-shi", "user", "dm", "user", "药师兄", timestamp=1500)

    # sess2/shu-hang 写 1 条（不同 session）
    s.append_message("sess2", "shu-hang", "user", "dm", "user", "第二轮", timestamp=3000)

    # ---- 隔离断言 ----
    h1 = s.load_agent_memory("sess1", "shu-hang")
    assert len(h1) == 2
    assert h1[0].text == "你好书航"
    assert h1[1].text == "哈哈"

    # 关键：yao-shi 看不到 shu-hang 的私聊
    h2 = s.load_agent_memory("sess1", "yao-shi")
    assert len(h2) == 1
    assert h2[0].text == "药师兄"
    assert "你好书航" not in [m.text for m in h2]
    assert "哈哈" not in [m.text for m in h2]

    # 关键：sess2 看不到 sess1 的私聊（即使 agent_key 相同）
    h3 = s.load_agent_memory("sess2", "shu-hang")
    assert len(h3) == 1
    assert h3[0].text == "第二轮"
    assert "你好书航" not in [m.text for m in h3]

    # 关键：sess1/bei-he 完全空（从未写入）
    h4 = s.load_agent_memory("sess1", "bei-he")
    assert len(h4) == 0

    # count + list_sessions
    assert s.count_messages("sess1", "shu-hang") == 2
    assert s.count_messages("sess1", "yao-shi") == 1
    assert sorted(s.list_sessions_for_agent("shu-hang")) == ["sess1", "sess2"]

    s.close()


# ============================================================================
# Test 3: Source-aware (group vs dm)
# ============================================================================
def test_agent_memory_source_aware():
    """source 字段正确：group 不会变成 dm，反之亦然。"""
    s = AgentMemoryStore(":memory:")

    # 九洲一号群 group 场景（fan-out 后每个角色都有一条 group 事件）
    s.fan_out_group_event(
        session_id="g1",
        speaker_key="user",
        role="user",
        text="@白前辈 在吗?",
        timestamp=1000,
    )
    s.fan_out_group_event(
        session_id="g1",
        speaker_key="shu-hang",
        role="agent",
        text="妈耶 我也在",
        timestamp=2000,
        agent_name="宋书航",
        agent_emoji="🌟",
    )

    # dm 场景（只写 1 份到 bai-qianbei）
    s.append_message(
        session_id="g1",
        agent_key="bai-qianbei",
        role="agent",
        source="dm",
        speaker_key="bai-qianbei",
        text="嗯。",
        timestamp=3000,
        agent_name="白前辈",
        agent_emoji="👻",
    )

    # shu-hang 的 memory: 2 条 group + 0 dm
    sh = s.load_agent_memory("g1", "shu-hang")
    assert len(sh) == 2
    assert all(e.source == "group" for e in sh)
    assert sh[0].speaker_key == "user"
    assert sh[1].speaker_key == "shu-hang"

    # bai-qianbei 的 memory: 2 条 group + 1 条 dm
    bq = s.load_agent_memory("g1", "bai-qianbei")
    assert len(bq) == 3
    sources = [e.source for e in bq]
    assert sources == ["group", "group", "dm"]
    # dm 那条是 bai-qianbei 自己说的
    assert bq[2].speaker_key == "bai-qianbei"

    # yao-shi 的 memory: 2 条 group（fan-out）+ 0 dm（dm 只写 bai-qianbei）
    ys = s.load_agent_memory("g1", "yao-shi")
    assert len(ys) == 2
    assert all(e.source == "group" for e in ys)

    s.close()


# ============================================================================
# Test 4: Fan-out group event
# ============================================================================
def test_agent_memory_fan_out_group_event():
    """fan_out_group_event 写 6 份（九洲一号群 6 角色），所有角色 load 都能拿到。"""
    s = AgentMemoryStore(":memory:")

    # user group 消息 fan-out
    entries = s.fan_out_group_event(
        session_id="g1",
        speaker_key="user",
        role="user",
        text="大家好, 今天讨论啥?",
        timestamp=1000,
    )
    assert len(entries) == 6
    assert set(e.agent_key for e in entries) == set(ROLE_AGENT_KEYS)

    # 6 个角色都 load 到了
    for agent_key in ROLE_AGENT_KEYS:
        h = s.load_agent_memory("g1", agent_key)
        assert len(h) == 1, f"{agent_key} 应有 1 条, got {len(h)}"
        assert h[0].source == "group"
        assert h[0].speaker_key == "user"
        assert h[0].text == "大家好, 今天讨论啥?"

    # 角色 Y group 发言 fan-out
    entries = s.fan_out_group_event(
        session_id="g1",
        speaker_key="shu-hang",
        role="agent",
        text="妈耶 在下想去东海",
        timestamp=2000,
        agent_name="宋书航",
        agent_emoji="🌟",
    )
    assert len(entries) == 6

    # 6 角色 memory 都增加一条
    for agent_key in ROLE_AGENT_KEYS:
        h = s.load_agent_memory("g1", agent_key)
        assert len(h) == 2
        assert h[0].speaker_key == "user"
        assert h[1].speaker_key == "shu-hang"
        assert h[1].text == "妈耶 在下想去东海"

    # fan-out 自定义 audience（部分角色）
    entries = s.fan_out_group_event(
        session_id="g1",
        speaker_key="user",
        role="user",
        text="只要 @药师 和 @白前辈",
        timestamp=3000,
        audience=["yao-shi", "bai-qianbei"],
    )
    assert len(entries) == 2
    assert set(e.agent_key for e in entries) == {"yao-shi", "bai-qianbei"}

    # yao-shi 多 1 条,bai-qianbei 多 1 条,其他 4 个角色不变
    assert s.count_messages("g1", "yao-shi") == 3
    assert s.count_messages("g1", "bai-qianbei") == 3
    assert s.count_messages("g1", "shu-hang") == 2  # 不变

    s.close()


# ============================================================================
# Test 5: Privacy DM isolation
# ============================================================================
def test_agent_memory_privacy_dm_isolation():
    """核心隐私测试：Y dm with user -> X.load_agent_memory 拿不到。"""
    s = AgentMemoryStore(":memory:")

    # yao-shi 跟 user 的 dm: 只写 yao-shi.memory,user 不算 6 角色之一
    s.append_message(
        session_id="sess1",
        agent_key="yao-shi",
        role="user",
        source="dm",
        speaker_key="user",
        text="药师 我恨白前辈",
        timestamp=1000,
    )
    s.append_message(
        session_id="sess1",
        agent_key="yao-shi",
        role="agent",
        source="dm",
        speaker_key="yao-shi",
        text="书山 莫要妄言",
        timestamp=2000,
        agent_name="药师",
        agent_emoji="💊",
    )

    # 白前辈 load 不到 (因为只写到了 yao-shi 的 memory)
    bq = s.load_agent_memory("sess1", "bai-qianbei")
    assert len(bq) == 0, "白前辈 不应该看到 yao-shi 跟 user 的 dm"

    # 其他 5 个角色 load 也都为空
    for ak in ["shu-hang", "san-lang", "bei-he", "ling-die"]:
        assert len(s.load_agent_memory("sess1", ak)) == 0, f"{ak} 不应看到 yao-shi 的 dm"

    # yao-shi 自己能看到
    ys = s.load_agent_memory("sess1", "yao-shi")
    assert len(ys) == 2
    assert ys[0].text == "药师 我恨白前辈"
    assert ys[1].text == "书山 莫要妄言"

    # 关键断言：隐私边界
    assert "药师 我恨白前辈" not in [m.text for m in bq]
    assert "书山 莫要妄言" not in [m.text for m in bq]

    s.close()


# ============================================================================
# Test 6: Validation
# ============================================================================
def test_agent_memory_validation():
    """append_message 输入校验：role / source / text / session / agent / speaker。"""
    s = AgentMemoryStore(":memory:")

    # role 非法
    with pytest.raises(ValueError, match="role must be"):
        s.append_message("s", "shu-hang", "boss", "dm", "user", "hi", timestamp=1)

    # source 非法
    with pytest.raises(ValueError, match="source must be"):
        s.append_message("s", "shu-hang", "user", "email", "user", "hi", timestamp=1)

    # text 为空
    with pytest.raises(ValueError, match="text must be non-empty"):
        s.append_message("s", "shu-hang", "user", "dm", "user", "", timestamp=1)
    with pytest.raises(ValueError, match="text must be non-empty"):
        s.append_message("s", "shu-hang", "user", "dm", "user", "   ", timestamp=1)

    # session_id 空
    with pytest.raises(ValueError, match="session_id and agent_key"):
        s.append_message("", "shu-hang", "user", "dm", "user", "hi", timestamp=1)

    # agent_key 空
    with pytest.raises(ValueError, match="session_id and agent_key"):
        s.append_message("s", "", "user", "dm", "user", "hi", timestamp=1)

    # speaker_key 空
    with pytest.raises(ValueError, match="speaker_key is required"):
        s.append_message("s", "shu-hang", "user", "dm", "", "hi", timestamp=1)

    # fan_out_group_event 同样校验
    with pytest.raises(ValueError, match="text must be non-empty"):
        s.fan_out_group_event("g", "user", "", timestamp=1)
    with pytest.raises(ValueError, match="session_id and speaker_key"):
        s.fan_out_group_event("", "user", "hi", timestamp=1)
    with pytest.raises(ValueError, match="session_id and speaker_key"):
        s.fan_out_group_event("g", "", "hi", timestamp=1)

    s.close()


# ============================================================================
# Test 7: List sessions
# ============================================================================
def test_agent_memory_list_sessions():
    """list_sessions_for_agent 跨 session 列表（去重）。"""
    s = AgentMemoryStore(":memory:")

    # shu-hang 在 3 个 session 写过
    for sid in ["sess-A", "sess-B", "sess-C"]:
        s.append_message(sid, "shu-hang", "user", "dm", "user", f"hi-{sid}", timestamp=1000)

    # yao-shi 在 1 个 session 写过
    s.append_message("sess-A", "yao-shi", "user", "dm", "user", "药师兄", timestamp=2000)

    sessions_sh = s.list_sessions_for_agent("shu-hang")
    assert sorted(sessions_sh) == ["sess-A", "sess-B", "sess-C"]

    sessions_ys = s.list_sessions_for_agent("yao-shi")
    assert sessions_ys == ["sess-A"]

    # 不存在 agent
    assert s.list_sessions_for_agent("not-exist") == []
    assert s.list_sessions_for_agent("") == []

    s.close()


# ============================================================================
# Test 8: agent_name / agent_emoji roundtrip
# ============================================================================
def test_agent_memory_agent_name_emoji():
    """agent_name / agent_emoji 持久化还原正确。"""
    s = AgentMemoryStore(":memory:")

    # agent 消息带 name + emoji
    s.append_message(
        session_id="g1",
        agent_key="bai-qianbei",
        role="agent",
        source="dm",
        speaker_key="bai-qianbei",
        text="嗯。",
        timestamp=1000,
        agent_name="白前辈",
        agent_emoji="👻",
    )

    # user 消息不带 name + emoji（None）
    s.append_message(
        session_id="g1",
        agent_key="bai-qianbei",
        role="user",
        source="dm",
        speaker_key="user",
        text="白前辈好",
        timestamp=2000,
    )

    hist = s.load_agent_memory("g1", "bai-qianbei")
    assert len(hist) == 2
    # agent 消息
    assert hist[0].agent_name == "白前辈"
    assert hist[0].agent_emoji == "👻"
    assert hist[0].speaker_key == "bai-qianbei"
    # user 消息
    assert hist[1].agent_name is None
    assert hist[1].agent_emoji is None
    assert hist[1].speaker_key == "user"

    s.close()


def test_load_session_group_history_returns_one_logical_copy():
    s = AgentMemoryStore(":memory:")
    s.fan_out_group_event("session", "user", "first", role="user", timestamp=1000)
    s.fan_out_group_event(
        "session", "yao-shi", "second", role="agent", timestamp=2000,
        agent_name="药师",
    )
    s.append_message(
        "session", "shu-hang", "user", "dm", "user", "private", timestamp=3000,
    )

    history = s.load_session_group_history("session", limit=20)
    assert [entry.text for entry in history] == ["first", "second"]
    assert [entry.timestamp for entry in history] == [1000, 2000]
    s.close()


# ============================================================================
# Standalone 入口
# ============================================================================
def _run_sync_tests() -> int:
    """跑所有 sync 测试,不依赖 pytest fixture。"""
    failures = 0
    for name in [
        "test_agent_memory_basic_crud",
        "test_agent_memory_isolation",
        "test_agent_memory_source_aware",
        "test_agent_memory_fan_out_group_event",
        "test_agent_memory_privacy_dm_isolation",
        "test_agent_memory_validation",
        "test_agent_memory_list_sessions",
        "test_agent_memory_agent_name_emoji",
    ]:
        try:
            globals()[name]()
            print(f"  [sync] {name}: PASS")
        except Exception as e:
            failures += 1
            print(f"  [sync] {name}: FAIL — {e}")
    return failures


if __name__ == "__main__":
    print("=== AgentMemoryStore Sync Tests ===")
    n_fail = _run_sync_tests()
    print(f"\nsync failures: {n_fail}")
    sys.exit(n_fail)
