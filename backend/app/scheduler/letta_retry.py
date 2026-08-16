"""Retry wrapper for ``_stream_via_letta`` with rate-limit (429) backoff.

Stage 8-NPC-Love (ADR-0007 — Option B) — 6 个 NPC loop 都会调到 Letta，
minimax M2.7-highspeed 没有官方 RPM 文档，但 production 已经看到过 429
（Stage 7 commit ``5fdfcc8`` 经验）。

设计要点：

1. **只对 HTTP 429 / RateLimitError 重试** — 其他异常（4xx 业务错误 / 5xx 服务
   端 bug / 超时）不算 rate limit，直接抛出，让 NPC loop 的兜底 try/except 接住。

2. **指数退避 + jitter** — 1s, 2s, 4s（最多 3 次），每次加随机 jitter（0..1s）
   避免 6 个 loop 同步重试。

3. **3 次失败后让位 5 分钟** — 调用方（NPC loop）收到 ``LettaRetryExhausted``
   后会把这个 loop 静默 sleep 5min，避免一个 bad NPC 饿死其他 5 个。

4. **接口形状与原 ``_stream_via_letta`` 对齐** — 它是 async generator，本模块
   暴露 ``stream_via_letta_with_retry(...)`` async generator，让 NPC loop
   ``async for`` 不变。

Public surface:

- ``stream_via_letta_with_retry(...)``  — retry-aware async generator
- ``LettaRetryExhausted``               — raised after all retries fail
"""
from __future__ import annotations

import asyncio
import logging
import random
from typing import Any, AsyncIterator


logger = logging.getLogger(__name__)


# ============================================================================
# Constants
# ============================================================================
_MAX_RETRIES: int = 3              # ADR-0007 §Option B scope: 3 retries
_BASE_BACKOFF_SECONDS: float = 1.0  # 1s, 2s, 4s ...
_JITTER_SECONDS: float = 1.0        # 0..1s uniform random
# Marker exception name(s) that count as "rate-limited".  We match by class name
# to avoid hard imports of upstream libs (which would create a circular import
# on app.graph → app.scheduler.letta_retry).  The grep below is intentional.
_RATE_LIMIT_NAMES: frozenset[str] = frozenset({
    "RateLimitError",
    "LettaRateLimitError",
    "HTTPStatusError",  # httpx raises this for 4xx/5xx — checked w/ status
})


# ============================================================================
# Exception types
# ============================================================================
class LettaRetryExhausted(Exception):
    """Raised after ``_MAX_RETRIES`` 429s in a row.

    Caller (NPC loop) should sleep 5 minutes before retrying to avoid starving
    other loops sharing the same upstream rate-limit budget.
    """


# ============================================================================
# Internal helpers
# ============================================================================
def _is_rate_limit_exception(exc: BaseException) -> bool:
    """Detect "rate-limited" via class name + status code.

    We can't hard-import ``langchain`` or ``openai`` exceptions here because
    that would create a cycle (app.graph → app.scheduler → app.graph).  So we
    inspect the exception's class name + (if present) ``status_code`` attr.

    Recognised:

    - ``RateLimitError`` / ``LettaRateLimitError`` (langchain / letta)
    - ``HTTPStatusError`` with ``status_code == 429`` (httpx)
    - Anything with a ``status_code == 429`` attribute (defensive)
    """
    cls_name = type(exc).__name__
    if cls_name in _RATE_LIMIT_NAMES:
        # For HTTPStatusError we still need to verify status == 429
        if cls_name == "HTTPStatusError":
            status = getattr(exc, "status_code", None) or getattr(exc, "response", None) and getattr(
                exc.response, "status_code", None
            )
            return status == 429
        # RateLimitError / LettaRateLimitError → always treat as rate limit
        return True
    # Defensive: anything that exposes status_code == 429
    status = getattr(exc, "status_code", None)
    if status == 429:
        return True
    return False


def _backoff_delay(attempt: int) -> float:
    """Compute the sleep duration before retry attempt ``attempt`` (0-indexed).

    Exponential: ``_BASE_BACKOFF_SECONDS * 2 ** attempt`` + jitter.
    ``attempt=0 → 1.0..2.0s``, ``attempt=1 → 2.0..3.0s``, ``attempt=2 → 4.0..5.0s``.
    """
    base = _BASE_BACKOFF_SECONDS * (2 ** attempt)
    return base + random.uniform(0.0, _JITTER_SECONDS)


# ============================================================================
# Public API
# ============================================================================
async def stream_via_letta_with_retry(
    *,
    role_key: str,
    session_id: str,
    all_msgs: list[Any],
    max_retries: int = _MAX_RETRIES,
    base_backoff: float = _BASE_BACKOFF_SECONDS,
    jitter: float = _JITTER_SECONDS,
) -> AsyncIterator[str]:
    """Wrap ``app.graph._stream_via_letta`` with rate-limit retry.

    Implements ADR-0007 §Option B scope item 3:

    > Wraps ``_stream_via_letta`` with retry-on-429 + exponential backoff +
    > jitter.  After 3 failed retries, loop sleeps 5 min before trying again.

    Yields:

        Same string chunks as ``_stream_via_letta`` (no buffering).

    Raises:

        ``LettaRetryExhausted`` — when ``max_retries`` consecutive 429s are seen.
        Any other exception — propagated immediately (no retry).

    Args:
        role_key / session_id / all_msgs — forwarded to ``_stream_via_letta``
        max_retries: how many 429 retries before giving up (default 3)
        base_backoff / jitter: override backoff timings (tests)
    """
    # Late import — keep module top-level cycle-free.  Tests monkeypatch
    # ``app.graph._stream_via_letta`` so this is the path they hit.
    from app.graph import _stream_via_letta

    attempt = 0
    while True:
        try:
            async for piece in _stream_via_letta(
                role_key=role_key,
                session_id=session_id,
                all_msgs=all_msgs,
            ):
                yield piece
            # Stream completed without error → success, exit retry loop.
            return
        except Exception as exc:  # noqa: BLE001 — let upstream decide
            if not _is_rate_limit_exception(exc):
                # Not a rate-limit error — propagate immediately, do NOT retry.
                raise
            attempt += 1
            if attempt > max_retries:
                logger.warning(
                    "[letta_retry] %s: exhausted %d retries — raising LettaRetryExhausted",
                    role_key, max_retries,
                )
                raise LettaRetryExhausted(
                    f"{role_key}: {max_retries} consecutive rate-limits",
                ) from exc
            # Exponential backoff with jitter (test overrides respected).
            base = base_backoff * (2 ** (attempt - 1))
            delay = base + random.uniform(0.0, jitter)
            logger.info(
                "[letta_retry] %s: rate-limited (attempt %d/%d), sleeping %.2fs",
                role_key, attempt, max_retries, delay,
            )
            await asyncio.sleep(delay)


__all__ = [
    "LettaRetryExhausted",
    "stream_via_letta_with_retry",
]