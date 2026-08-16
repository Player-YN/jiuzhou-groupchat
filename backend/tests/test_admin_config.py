"""Tests for GET/POST /api/admin/config.

覆盖:
- GET 返当前 provider/model/key (key redacted as '****xxxx')
- POST 写新 provider/model/api_key 到 .env (atomic write)
- POST 切 provider 时正确处理 USE_MOCK_LLM
- POST 后 LRU cache 被清,get_settings() 读到新值
- POST 拒收未知 provider / 不支持字段的 provider
- _update_env_value / _read_env_value 单元行为
- 写 key 含特殊字符 (空格/=/#) 时正确 quote
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings, set_active_env_path
from app.main import app
from app.routers import admin_config


# Env keys that the live .env might have set, which would shadow our tmp
# fixture's values.  We delete them on fixture setup so the tmp .env is
# the only source of truth.
_LIVE_KEYS_TO_CLEAR = [
    "USE_MOCK_LLM", "MINIMAX_API_KEY", "MINIMAX_MODEL", "MINIMAX_M3_MODEL",
    "MINIMAX_BASE_URL", "AGNES_API_KEY", "AGNES_MODEL", "AGNES_BASE_URL",
    "OPENAI_API_KEY", "DEEPSEEK_API_KEY", "ANTHROPIC_API_KEY", "USE_LETTA",
    "ACTIVE_PROVIDER",
]


@pytest.fixture
def env_path(tmp_path, monkeypatch):
    """Redirect all .env reads/writes to a tmp .env file."""
    # Clear process env pollution from the live .env so the tmp .env
    # is the only source of truth for these keys.
    for k in _LIVE_KEYS_TO_CLEAR:
        monkeypatch.delenv(k, raising=False)

    p = tmp_path / ".env"
    p.write_text(
        "USE_MOCK_LLM=false\n"
        "MINIMAX_API_KEY=sk-original-key\n"
        "MINIMAX_M3_MODEL=MiniMax-M3\n"
        "MINIMAX_BASE_URL=https://api.minimaxi.com/v1\n"
        "AGNES_API_KEY=\n"
        "AGNES_MODEL=agnes-2.0-flash\n"
        "AGNES_BASE_URL=https://apihub.agnes-ai.com/v1\n",
        encoding="utf-8",
    )
    set_active_env_path(p)
    yield p
    set_active_env_path(None)


@pytest.fixture
def client(env_path):
    return TestClient(app)


# ===== GET =====

def test_get_returns_current_provider_and_redacted_key(client):
    r = client.get("/api/admin/config")
    assert r.status_code == 200
    data = r.json()
    assert data["provider"] == "minimax"  # MINIMAX_API_KEY is set, AGNES_API_KEY is empty
    assert data["model"] == "MiniMax-M3"
    assert data["base_url"] == "https://api.minimaxi.com/v1"
    # Original key is 'sk-original-key'; last 4 = 'r-key'
    assert data["api_key_redacted"] == "****-key"
    assert data["use_mock_llm"] is False
    assert data["use_letta"] is True  # default


def test_get_redacts_empty_key_as_none(client):
    # Clear the key first via POST
    client.post("/api/admin/config", json={"api_key": ""})
    r = client.get("/api/admin/config")
    assert r.status_code == 200
    # After clearing minimax_api_key, falls back to agnes (which has no key in tmp .env),
    # so we'd see agnes.  But wait — tmp .env has AGNES_API_KEY="" so agnes also has no key,
    # then openai has no key, all the way down to mock.  So provider should be "mock".
    data = r.json()
    assert data["api_key_redacted"] is None
    # Provider will have fallen back to mock because all keys are empty
    assert data["provider"] == "mock"


# ===== POST — happy path =====

def test_post_updates_model(client, env_path):
    r = client.post("/api/admin/config", json={"model": "MiniMax-M4-turbo"})
    assert r.status_code == 200
    data = r.json()
    assert data["model"] == "MiniMax-M4-turbo"
    # Verify .env on disk was actually updated
    text = env_path.read_text(encoding="utf-8")
    assert "MINIMAX_M3_MODEL=MiniMax-M4-turbo" in text
    # Original key still present
    assert "MINIMAX_API_KEY=sk-original-key" in text


def test_post_updates_api_key(client, env_path):
    r = client.post("/api/admin/config", json={"api_key": "sk-new-secret-1234"})
    assert r.status_code == 200
    data = r.json()
    # New key ends in '1234' -> "****1234"
    assert data["api_key_redacted"] == "****1234"
    text = env_path.read_text(encoding="utf-8")
    assert "MINIMAX_API_KEY=sk-new-secret-1234" in text


def test_post_clears_api_key_with_empty_string(client, env_path):
    r = client.post("/api/admin/config", json={"api_key": ""})
    assert r.status_code == 200
    data = r.json()
    assert data["api_key_redacted"] is None
    text = env_path.read_text(encoding="utf-8")
    # Empty value should be preserved as MINIMAX_API_KEY= (not removed)
    assert "MINIMAX_API_KEY=" in text


def test_post_updates_base_url(client, env_path):
    r = client.post("/api/admin/config", json={"base_url": "https://custom.example.com/v1"})
    assert r.status_code == 200
    assert r.json()["base_url"] == "https://custom.example.com/v1"
    text = env_path.read_text(encoding="utf-8")
    assert "MINIMAX_BASE_URL=https://custom.example.com/v1" in text


# ===== POST — provider switching =====

def test_post_switch_provider_preserves_existing_fields(client, env_path):
    """Switching to agnes should set the explicit ACTIVE_PROVIDER override
    so the user choice wins over the auto-detected chain (even if the
    target provider has no key — the front-end will display "no key
    configured" so the user can fix it)."""
    r = client.post("/api/admin/config", json={"provider": "agnes"})
    assert r.status_code == 200
    data = r.json()
    # ACTIVE_PROVIDER=agnes forces the choice; agnes_api_key is empty but
    # the user explicitly asked for agnes, so the front-end should
    # surface the "no key" state and prompt for one.
    assert data["provider"] == "agnes"
    assert data["api_key_redacted"] is None
    # Switching to a non-mock provider from a non-mock state should not have
    # flipped USE_MOCK_LLM; original USE_MOCK_LLM=false should remain.
    text = env_path.read_text(encoding="utf-8")
    assert "USE_MOCK_LLM=false" in text
    # ACTIVE_PROVIDER was persisted so the choice survives reload
    assert "ACTIVE_PROVIDER=agnes" in text


def test_post_switch_to_mock_flips_use_mock_llm(client, env_path):
    r = client.post("/api/admin/config", json={"provider": "mock"})
    assert r.status_code == 200
    data = r.json()
    assert data["provider"] == "mock"
    assert data["use_mock_llm"] is True
    text = env_path.read_text(encoding="utf-8")
    assert "USE_MOCK_LLM=true" in text


def test_post_switch_to_real_provider_from_mock_flips_use_mock_llm(client, env_path):
    # First turn mock on
    client.post("/api/admin/config", json={"provider": "mock"})
    # Then switch to agnes with explicit key
    r = client.post(
        "/api/admin/config",
        json={"provider": "agnes", "api_key": "sk-agnes-fresh"},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["provider"] == "agnes"
    assert data["use_mock_llm"] is False
    # DEBUG
    print("DEBUG data:", data)
    print("DEBUG env:", env_path.read_text(encoding="utf-8"))
    assert data["api_key_redacted"] == "****resh"


# ===== POST — hot-reload =====

def test_post_clears_get_settings_cache(client):
    """After POST, get_settings() must return the new values, not stale ones."""
    s_before = get_settings()
    assert s_before.active_provider == "minimax"
    assert s_before.minimax_m3_model == "MiniMax-M3"

    r = client.post(
        "/api/admin/config",
        json={"model": "MiniMax-M3.5", "api_key": "sk-1234567"},
    )
    assert r.status_code == 200

    s_after = get_settings()
    assert s_after.minimax_m3_model == "MiniMax-M3.5"
    assert s_after.minimax_api_key == "sk-1234567"


# ===== POST — validation =====

def test_post_rejects_unknown_provider(client):
    r = client.post("/api/admin/config", json={"provider": "gpt-9000"})
    assert r.status_code == 400
    assert "Unknown provider" in r.json()["detail"]


def test_post_rejects_model_for_mock(client, env_path):
    r = client.post("/api/admin/config", json={"provider": "mock", "model": "gpt-4"})
    assert r.status_code == 400
    assert "does not accept a model" in r.json()["detail"]


def test_post_rejects_api_key_for_ollama(client, env_path):
    r = client.post("/api/admin/config", json={"provider": "ollama", "api_key": "sk-anything"})
    assert r.status_code == 400
    assert "does not accept an api_key" in r.json()["detail"]


# ===== Unit: _update_env_value / _read_env_value =====

def test_update_env_value_preserves_other_lines(env_path):
    env_path.write_text(
        "# top comment\n"
        "FOO=bar\n"
        "\n"
        "BAZ=qux\n",
        encoding="utf-8",
    )
    admin_config._update_env_value(env_path, "FOO", "new-value")
    text = env_path.read_text(encoding="utf-8")
    assert "# top comment" in text
    assert "FOO=new-value" in text
    assert "BAZ=qux" in text
    assert text.count("\n") >= 3  # not collapsed


def test_update_env_value_quotes_value_with_special_chars(env_path):
    env_path.write_text("KEY=old\n", encoding="utf-8")
    # value with =, #, and " inside
    admin_config._update_env_value(env_path, "KEY", 'a=b#c"d')
    text = env_path.read_text(encoding="utf-8")
    assert 'KEY="a=b#c\\"d"' in text or 'KEY="a=b#c\"d"' in text or 'KEY="a=b#c""d"' in text
    # And reading it back should give us the original value
    assert admin_config._read_env_value(env_path, "KEY") == 'a=b#c"d'


def test_update_env_value_appends_when_key_absent(env_path):
    env_path.write_text("FOO=bar\n", encoding="utf-8")
    admin_config._update_env_value(env_path, "NEW_KEY", "value1")
    text = env_path.read_text(encoding="utf-8")
    assert "FOO=bar" in text
    assert "NEW_KEY=value1" in text


def test_update_env_value_handles_quoted_existing_value(env_path):
    env_path.write_text('KEY="already-quoted"\n', encoding="utf-8")
    admin_config._update_env_value(env_path, "KEY", "newval")
    text = env_path.read_text(encoding="utf-8")
    # New value (no special chars) should not be quoted
    assert "KEY=newval" in text
    assert "already-quoted" not in text


def test_update_env_value_creates_file_if_missing(tmp_path):
    p = tmp_path / "fresh.env"
    admin_config._update_env_value(p, "K", "V")
    assert p.read_text(encoding="utf-8") == "K=V\n"


def test_update_env_value_atomic_no_tmp_leftover(env_path):
    """After update, the .env.tmp file should be cleaned up via os.replace."""
    env_path.write_text("FOO=bar\n", encoding="utf-8")
    admin_config._update_env_value(env_path, "FOO", "baz")
    assert not (env_path.parent / f"{env_path.name}.tmp").exists()


# ===== Redaction =====

def test_redact_key_handles_short_values():
    assert admin_config._redact_key(None) is None
    assert admin_config._redact_key("") is None
    assert admin_config._redact_key("abcd") == "****"
    assert admin_config._redact_key("sk-verylongkey") == "****gkey"
