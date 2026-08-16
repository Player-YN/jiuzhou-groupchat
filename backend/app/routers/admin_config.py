"""Admin endpoints for runtime LLM provider configuration.

Routes:

- ``GET  /api/admin/config``  — current provider/model/key/base_url snapshot.
  API keys are REDACTED (last 4 chars shown only) so the front-end can
  display "configured" without leaking secrets into JSON responses.

- ``POST /api/admin/config`` — write a new provider/model/api_key/base_url
  combo to the active ``.env`` file and clear the ``get_settings()`` LRU
  cache so the next request re-reads the new values.  Behaviour is
  "save-and-replace, no confirm" per product spec.

The .env write is atomic (write to ``.env.tmp`` then ``os.replace``) so
a partial write never leaves the file in a half-baked state.
"""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.config import get_active_env_path, get_settings, set_active_env_path

logger = logging.getLogger(__name__)

router = APIRouter()

# --- Provider schema ---
#
# We expose one provider slot at a time: the "active" provider whose model
# + key + base_url are user-editable.  Per-role routing (4 minimax + 2
# agnes for the six 九洲一号群 NPCs) is hard-coded in graph.py; the UI
# controls the GLOBAL provider/model, which is what the summarizer + any
# non-routed code path uses.
#
# We use plain ``str`` (not ``Literal``) for the API so unknown values
# return our manual 400 with a helpful message, instead of FastAPI's
# generic 422 schema-validation error.
KNOWN_PROVIDERS = ("mock", "minimax", "agnes", "openai", "deepseek", "anthropic", "ollama")

# Field mapping per provider: which .env keys back the user-visible
# (provider, model, base_url, api_key) tuple.
# `model_key` and `base_url_key` are None for providers that don't use them
# (e.g. mock).  `api_key_field` is the same as alias for everything except
# `openai` which uses `openai_api_key` (alias), `ollama` which has no key.
_PROVIDER_FIELDS: dict[str, dict] = {
    "mock": {"model_key": None, "base_url_key": None, "api_key_field": None},
    "minimax": {
        "model_key": "minimax_m3_model",   # Stage 4-A default
        "base_url_key": "minimax_base_url",
        "api_key_field": "minimax_api_key",
    },
    "agnes": {
        "model_key": "agnes_model",
        "base_url_key": "agnes_base_url",
        "api_key_field": "agnes_api_key",
    },
    "openai": {
        "model_key": "openai_model",
        "base_url_key": "openai_base_url",
        "api_key_field": "openai_api_key",
    },
    "deepseek": {
        "model_key": "deepseek_model",
        "base_url_key": "deepseek_base_url",
        "api_key_field": "deepseek_api_key",
    },
    "anthropic": {
        "model_key": "anthropic_model",
        "base_url_key": None,
        "api_key_field": "anthropic_api_key",
    },
    "ollama": {
        "model_key": "ollama_model",
        "base_url_key": "ollama_base_url",
        "api_key_field": None,
    },
}

# .env file location is resolved at runtime via get_active_env_path() /
# set_active_env_path() from app.config.  This lets tests redirect reads
# and writes to a tmp .env without monkeypatching the file system, and
# gives the router a single source of truth for "where do we write?".
def _resolve_env_path() -> Path:
    """Pick which .env file to write to. Delegates to config module."""
    return get_active_env_path()


def _redact_key(value: Optional[str]) -> Optional[str]:
    """Redact an API key for the GET response.

    Returns None for None/empty, or ``"****XXXX"`` showing only the last 4
    chars so the front-end can render "configured: sk-...AbCd" without
    exposing the secret.
    """
    if not value:
        return None
    if len(value) <= 4:
        return "****"
    return f"****{value[-4:]}"


def _read_env_value(path: Path, key: str) -> Optional[str]:
    """Read a single key from a .env file, returning None if not found."""
    if not path.exists():
        return None
    pattern = re.compile(rf"^\s*{re.escape(key)}\s*=\s*(.*?)\s*$", re.MULTILINE)
    for line in path.read_text(encoding="utf-8").splitlines():
        m = pattern.match(line)
        if m:
            raw = m.group(1)
            # strip surrounding quotes if present
            if (raw.startswith('"') and raw.endswith('"')) or (
                raw.startswith("'") and raw.endswith("'")
            ):
                raw = raw[1:-1]
            return raw
    return None


def _update_env_value(path: Path, key: str, value: str) -> None:
    """Update or append ``KEY=value`` in a .env file, atomically.

    Preserves all other lines, comments, and blank lines.  Uses
    ``os.replace`` for the atomic swap so a partial write can never
    leave a half-baked .env.
    """
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{key}={value}\n", encoding="utf-8")
        return

    text = path.read_text(encoding="utf-8")
    # Match a single line `^KEY=...` up to end-of-line.
    # Critical: ``\s`` inside the leading group would gobble the trailing
    # newline of an empty `KEY=` value, then ``[^\n]*?`` would match
    # the *next* line's content.  We restrict whitespace to [ \t] so
    # we never cross a line boundary.
    pattern = re.compile(
        rf"^[ \t]*{re.escape(key)}[ \t]*=[ \t]*([^\n]*?)[ \t]*{chr(36)}",
        re.MULTILINE,
    )

    # Quote the value if it contains spaces, '#', or '=', so .env parsers
    # don't choke on edge cases (e.g. an api_key with an embedded '=').
    needs_quote = any(c in value for c in (" ", "#", "=", '"', "'"))
    encoded = f'"{value}"' if needs_quote else value

    if pattern.search(text):
        # group(1) is the value portion only (the leading KEY= was
        # matched but not captured).  Replace just the value.
        new_text = pattern.sub(lambda m: m.group(0)[: m.group(0).rfind("=") + 1] + encoded, text)
    else:
        # Append a new key=value line, with a leading blank line if the
        # file doesn't already end with one.
        sep = "" if text.endswith("\n\n") or not text else "\n"
        new_text = f"{text}{sep}{key}={encoded}\n"

    # Atomic write: write to .env.tmp then os.replace.
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(new_text, encoding="utf-8")
    os.replace(tmp, path)


def _resolve_active_model(s) -> str:
    """Return the model name the active provider is using right now."""
    p = s.active_provider
    field = _PROVIDER_FIELDS.get(p, {})
    model_key = field.get("model_key")
    if not model_key:
        return ""
    return getattr(s, model_key, "") or ""


def _resolve_active_base_url(s) -> str:
    """Return the base_url the active provider is using right now."""
    p = s.active_provider
    field = _PROVIDER_FIELDS.get(p, {})
    base_url_key = field.get("base_url_key")
    if not base_url_key:
        return ""
    return getattr(s, base_url_key, "") or ""


def _resolve_active_api_key_redacted(s) -> Optional[str]:
    """Return the (redacted) api key for the active provider."""
    p = s.active_provider
    field = _PROVIDER_FIELDS.get(p, {})
    api_key_field = field.get("api_key_field")
    if not api_key_field:
        return None
    return _redact_key(getattr(s, api_key_field, None))


def _resolve_raw_key_for_provider(s, provider: str) -> Optional[str]:
    """Return the RAW (unredacted) key for a given provider — internal helper."""
    field = _PROVIDER_FIELDS.get(provider, {})
    api_key_field = field.get("api_key_field")
    if not api_key_field:
        return None
    return getattr(s, api_key_field, None)


# === Pydantic schemas ===

class ConfigSnapshot(BaseModel):
    """Current provider/model/key snapshot returned by GET."""

    provider: str = Field(..., description="Currently active provider name")
    model: str = Field(..., description="Currently active model")
    base_url: str = Field(default="", description="Currently active base URL (empty if N/A)")
    api_key_redacted: Optional[str] = Field(
        default=None, description="API key, redacted as '****xxxx'"
    )
    use_mock_llm: bool = Field(..., description="Whether USE_MOCK_LLM is forcing mock")
    use_letta: bool = Field(..., description="Whether Letta integration is enabled")
    env_path: str = Field(..., description="Absolute path of the .env file we'd write to")


class ConfigUpdate(BaseModel):
    """Body of POST /api/admin/config — direct-save, no confirm.

    All four fields are optional; only provided ones are written.  Pass
    ``api_key=""`` to clear the active provider's key (forces fallback to
    the next provider in priority order).
    """

    provider: Optional[str] = Field(
        default=None,
        description="Switch the active provider (e.g. 'minimax', 'agnes'). Unknown values return 400.",
    )
    model: Optional[str] = Field(default=None, description="Set the active model name")
    base_url: Optional[str] = Field(default=None, description="Set the active base URL")
    api_key: Optional[str] = Field(
        default=None,
        description="Set/clear the active API key. Pass empty string to clear.",
    )


# === Routes ===

@router.get("/api/admin/config", response_model=ConfigSnapshot)
async def get_config() -> ConfigSnapshot:
    """Return the current LLM provider configuration (redacted)."""
    s = get_settings()
    return ConfigSnapshot(
        provider=s.active_provider,
        model=_resolve_active_model(s),
        base_url=_resolve_active_base_url(s),
        api_key_redacted=_resolve_active_api_key_redacted(s),
        use_mock_llm=s.use_mock_llm,
        use_letta=s.use_letta,
        env_path=str(_resolve_env_path()),
    )


@router.post("/api/admin/config", response_model=ConfigSnapshot)
async def post_config(body: ConfigUpdate) -> ConfigSnapshot:
    """Write a new provider/model/api_key/base_url to ``.env`` and hot-reload.

    Behaviour is "save-and-replace, no confirm" per product spec: the
    first call lands on disk, no further interaction required.  Per-role
    routing (4 minimax + 2 agnes in ``graph.py``) is NOT changed by this
    endpoint — it controls the GLOBAL active provider used by the
    summarizer and any non-routed code path.
    """
    # If a provider switch is requested but no model/api_key given, default
    # to the NEW provider's existing model + key (preserve user's prior
    # choice for that provider if they had one configured).
    s = get_settings()
    target_provider: str = body.provider or s.active_provider
    if target_provider not in _PROVIDER_FIELDS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown provider: {target_provider!r}",
        )

    fields = _PROVIDER_FIELDS[target_provider]
    env_path = _resolve_env_path()

    # ---- Apply changes ----
    # Switch provider first (writes USE_MOCK_LLM + ACTIVE_PROVIDER so the
    # user-visible choice wins over the auto-detected chain on next load).
    if body.provider is not None and body.provider != s.active_provider:
        if body.provider == "mock":
            # Switch TO mock means turning USE_MOCK_LLM on
            _update_env_value(env_path, "USE_MOCK_LLM", "true")
        else:
            # Switch AWAY from mock means turning USE_MOCK_LLM off
            if s.use_mock_llm:
                _update_env_value(env_path, "USE_MOCK_LLM", "false")
        # Persist the explicit override so reload keeps this provider
        # even if other provider keys are still set.
        _update_env_value(env_path, "ACTIVE_PROVIDER", body.provider)

    # Model
    if body.model is not None:
        model_key = fields.get("model_key")
        if not model_key:
            raise HTTPException(
                status_code=400,
                detail=f"Provider {target_provider!r} does not accept a model override",
            )
        _update_env_value(env_path, model_key.upper(), body.model)

    # Base URL
    if body.base_url is not None:
        base_url_key = fields.get("base_url_key")
        if not base_url_key:
            raise HTTPException(
                status_code=400,
                detail=f"Provider {target_provider!r} does not accept a base_url override",
            )
        _update_env_value(env_path, base_url_key.upper(), body.base_url)

    # API key (None = leave alone; "" = clear; non-empty = set)
    if body.api_key is not None:
        api_key_field = fields.get("api_key_field")
        if not api_key_field:
            raise HTTPException(
                status_code=400,
                detail=f"Provider {target_provider!r} does not accept an api_key",
            )
        _update_env_value(env_path, api_key_field.upper(), body.api_key)

    # ---- Hot-reload: clear the LRU cache so get_settings() re-reads ----
    get_settings.cache_clear()

    # Reload to return a fresh snapshot
    s_new = get_settings()
    logger.info(
        "[admin_config] config updated: provider=%s -> %s, model=%s, "
        "key_set=%s, env=%s",
        s.active_provider,
        s_new.active_provider,
        _resolve_active_model(s_new),
        "yes" if _resolve_raw_key_for_provider(s_new, s_new.active_provider) else "no",
        env_path,
    )

    return ConfigSnapshot(
        provider=s_new.active_provider,
        model=_resolve_active_model(s_new),
        base_url=_resolve_active_base_url(s_new),
        api_key_redacted=_resolve_active_api_key_redacted(s_new),
        use_mock_llm=s_new.use_mock_llm,
        use_letta=s_new.use_letta,
        env_path=str(env_path),
    )
