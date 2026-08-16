"""应用配置：通过 .env 加载，禁止硬编码 API key。"""
from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# 仓库根的 .env 优先于 backend/.env
_REPO_ROOT = Path(__file__).resolve().parents[2]
_BACKEND_ENV = Path(__file__).resolve().parents[1] / ".env"

# Runtime-overridable env path: set by admin_config router when it needs to
# redirect reads/writes to a different .env (e.g. in tests).  None means
# "use the default resolution order" (backend/.env first, then repo/.env).
_runtime_env_path: Path | None = None
# Track which env keys we loaded from .env so subsequent loads can
# OVERWRITE them (process env still wins on the very first load).
_ENV_LOADED_KEYS: set[str] = set()


def _resolve_default_env_path() -> Path:
    """Default .env file resolution: backend/.env if it exists, else repo/.env."""
    if _BACKEND_ENV.exists():
        return _BACKEND_ENV
    return _REPO_ROOT / ".env"


def get_active_env_path() -> Path:
    """Return the .env file that Settings will read from right now.

    Allows admin_config + tests to override via ``set_active_env_path()``.
    """
    return _runtime_env_path or _resolve_default_env_path()


def set_active_env_path(path: Path | None) -> None:
    """Override the .env path used by ``Settings`` (set ``None`` to reset)."""
    global _runtime_env_path
    if path is not None and not isinstance(path, Path):
        path = Path(path)
    _runtime_env_path = path
    # Also clear the LRU cache so the next get_settings() call rebuilds.
    get_settings.cache_clear()


# Initial dotenv load (preserves historical "root .env preferred" behaviour
# IF backend/.env doesn't exist yet; otherwise the active path is backend/.env).
load_dotenv(_resolve_default_env_path(), override=False)


def _load_env_into_environ(path: Path) -> None:
    """Parse a .env file and inject KEY=VALUE pairs into os.environ.

    Behaviour: process env vars take precedence on FIRST load (the standard
    pydantic-settings semantics).  On RE-load (cache cleared via
    set_active_env_path or after admin_config POST), values from the .env
    file OVERWRITE os.environ so the just-written values are seen.

    We track which keys came from .env via ``_ENV_LOADED_KEYS`` and only
    override those on subsequent loads.
    """
    global _ENV_LOADED_KEYS
    if path is not None and not isinstance(path, Path):
        path = Path(path)
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"^([A-Z_][A-Z0-9_]*)\s*=\s*(.*?)\s*$", line, re.IGNORECASE)
        if not m:
            continue
        key, val = m.group(1).upper(), m.group(2)
        # Strip surrounding quotes
        if (val.startswith('"') and val.endswith('"')) or (
            val.startswith("'") and val.endswith("'")
        ):
            val = val[1:-1]
        if key in _ENV_LOADED_KEYS:
            # Subsequent load: .env is authoritative for these keys
            os.environ[key] = val
        else:
            # First load: process env wins for the VALUE, but we
            # remember the key so we can override on next load
            # (process env can be reloaded from .env if user POSTs
            # a new value via admin_config).
            _ENV_LOADED_KEYS.add(key)
            if key not in os.environ:
                os.environ[key] = val


class Settings(BaseSettings):
    """运行时配置。所有值来自环境变量 / .env。"""

    model_config = SettingsConfigDict(
        env_file=None,  # populated in __init__ via os.environ
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ===== LLM Providers =====

    openai_api_key: Optional[str] = Field(default=None, alias="OPENAI_API_KEY")
    openai_model: str = Field(default="gpt-4o-mini", alias="OPENAI_MODEL")
    openai_base_url: str = Field(
        default="https://api.openai.com/v1", alias="OPENAI_BASE_URL"
    )

    deepseek_api_key: Optional[str] = Field(default=None, alias="DEEPSEEK_API_KEY")
    deepseek_model: str = Field(default="deepseek-chat", alias="DEEPSEEK_MODEL")
    deepseek_base_url: str = Field(
        default="https://api.deepseek.com/v1", alias="DEEPSEEK_BASE_URL"
    )

    anthropic_api_key: Optional[str] = Field(default=None, alias="ANTHROPIC_API_KEY")
    anthropic_model: str = Field(
        default="claude-3-5-sonnet-20241022", alias="ANTHROPIC_MODEL"
    )

    ollama_base_url: str = Field(
        default="http://localhost:11434", alias="OLLAMA_BASE_URL"
    )
    ollama_model: str = Field(default="llama3.1:8b", alias="OLLAMA_MODEL")

    # MiniMax (OpenAI-compatible; Token Plan sk-cp-* prefix)
    minimax_api_key: Optional[str] = Field(default=None, alias="MINIMAX_API_KEY")
    minimax_model: str = Field(default="MiniMax-M2.7-highspeed", alias="MINIMAX_MODEL")
    minimax_m3_model: str = Field(default="MiniMax-M3", alias="MINIMAX_M3_MODEL")
    minimax_base_url: str = Field(
        default="https://api.minimaxi.com/v1", alias="MINIMAX_BASE_URL"
    )

    # Agnes AI (OpenAI-compatible)
    agnes_api_key: Optional[str] = Field(default=None, alias="AGNES_API_KEY")
    agnes_model: str = Field(default="agnes-2.0-flash", alias="AGNES_MODEL")
    agnes_base_url: str = Field(
        default="https://apihub.agnes-ai.com/v1", alias="AGNES_BASE_URL"
    )

    # ===== Runtime =====
    use_mock_llm: bool = Field(default=False, alias="USE_MOCK_LLM")
    backend_host: str = Field(default="0.0.0.0", alias="BACKEND_HOST")
    backend_port: int = Field(default=8000, alias="BACKEND_PORT")
    backend_reload: bool = Field(default=True, alias="BACKEND_RELOAD")

    # ===== Stage 7 Letta integration =====
    letta_base_url: str = Field(
        default="http://letta:8283", alias="LETTA_BASE_URL"
    )
    letta_api_key: str = Field(
        default="letta_dev_password", alias="LETTA_API_KEY"
    )
    letta_llm_model: str = Field(
        default="openai-proxy/MiniMax-M2.7-highspeed", alias="LETTA_LLM_MODEL"
    )
    letta_minimax_api_key: Optional[str] = Field(
        default=None, alias="LETTA_MINIMAX_API_KEY"
    )
    use_letta: bool = Field(default=True, alias="USE_LETTA")

    # `active_provider` is populated by the `_resolve_active_provider`
    # model_validator below.  Declared as Optional[str] here so Pydantic
    # treats it as a regular instance attribute (Pydantic v2 @property
    # accessors don't work the way v1 did).
    active_provider: Optional[str] = None

    def __init__(self, **kwargs):
        # Pydantic v2's BaseSettings reads `env_file` from the class-level
        # ``model_config`` at __pydantic_validator__ build time, NOT at
        # __init__ time.  We can't mutate it per-instance.  Instead we
        # populate ``os.environ`` from the active .env BEFORE calling
        # super().__init__() — BaseSettings picks up os.environ as the
        # only file-source when env_file=None.
        active_path = get_active_env_path()
        if active_path and active_path.exists():
            _load_env_into_environ(active_path)
        super().__init__(**kwargs)

    # If set, forces the active provider to this value (admin_config uses
    # this so the user-visible choice wins over the auto-detected chain).
    active_provider_override: Optional[str] = Field(
        default=None, alias="ACTIVE_PROVIDER"
    )

    @model_validator(mode="after")
    def _resolve_active_provider(self) -> "Settings":
        """Populate ``active_provider`` from the priority chain after
        all fields are loaded.

        Priority:
          1. ``ACTIVE_PROVIDER`` (explicit user override, set by admin_config)
          2. use_mock_llm=True → "mock"
          3. minimax > agnes > openai > anthropic > deepseek
          4. fallback "mock" when no key set
        """
        explicit = (self.active_provider_override or "").strip().lower()
        if explicit in ("mock", "minimax", "agnes", "openai", "deepseek", "anthropic", "ollama"):
            chosen = explicit
        elif self.use_mock_llm:
            chosen = "mock"
        elif self.minimax_api_key:
            chosen = "minimax"
        elif self.agnes_api_key:
            chosen = "agnes"
        elif self.openai_api_key:
            chosen = "openai"
        elif self.anthropic_api_key:
            chosen = "anthropic"
        elif self.deepseek_api_key:
            chosen = "deepseek"
        else:
            chosen = "mock"  # fallback when no key set
        object.__setattr__(self, "active_provider", chosen)
        return self

    @property
    def effective_letta_minimax_api_key(self) -> Optional[str]:
        """Resolve the Letta provider API key.

        Priority:
          1. `LETTA_MINIMAX_API_KEY` (explicit, set by `Settings`)
          2. `MINIMAX_API_KEY` (legacy env name fallback)
          3. `None`
        """
        if self.letta_minimax_api_key:
            return self.letta_minimax_api_key
        return os.environ.get("MINIMAX_API_KEY")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    s = Settings()
    # 启动时打印（隐藏 key）
    print(
        f"[config] provider={s.active_provider} model={s.openai_model} "
        f"mock={s.use_mock_llm} use_letta={s.use_letta} "
        f"letta={s.letta_base_url} letta_model={s.letta_llm_model}"
    )
    return s
