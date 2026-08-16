"""Group chat concurrency primitives.

Stage 8-NPC-Love（ADR-0007 — Option B）— 6 个 NPC 自主 loop 在同一个 group 上发言，
需要一个集中的并发控制层防止"两个 NPC 同时喷"和"刷屏"。

设计要点：

1. **asyncio.Semaphore(1)** — 同一时刻最多 1 个 NPC 在调用
   ``_stream_via_letta``。让 LLM 调用之间留出间隔，避免 minimax M2.7-highspeed
   触发 429（rate limit）。

2. **10s 冷却（last_push_at lock）** — 即便拿到信号量，也必须距上次 push 至少
   10s 过去（cool-down）。这是 ADR-0007 §Acceptance criteria 6 硬要求：
   "6 loops 跑 1 分钟，两个 NPC 的发言间隔不会小于 10s"。

3. **失败不影响 lock** — 任何 wait / acquire 路径若抛异常，semaphore 内部状态
   不会破坏（Python `asyncio.Semaphore` 在取消时会自动 release）。

Public surface:

- ``GroupChatSemaphore``  — singleton concurrency primitive
- ``get_group_semaphore()`` / ``set_group_semaphore()``
"""
from __future__ import annotations

import asyncio
import threading
import time
from dataclasses import dataclass


# Minimum gap between consecutive group pushes (seconds).  ADR-0007 §Acceptance 6
# pins this at 10s — same-NPC and cross-NPC alike.
_COOLDOWN_SECONDS: float = 10.0


@dataclass
class _Slot:
    """Internal — wraps the asyncio primitives so they're all created lazily
    inside a running event loop.

    asyncio primitives (Semaphore / Lock) are bound to the loop at construction
    time, so we defer creation until first use.  The first ``acquire`` call from
    an async context triggers `_ensure_loop()` which builds both.
    """

    semaphore: asyncio.Semaphore | None = None
    cooldown_lock: asyncio.Lock | None = None
    last_push_at: float = 0.0


class GroupChatSemaphore:
    """Process-wide single-flight + cool-down gate for the group chat push path.

    Usage (async context):

        sem = get_group_semaphore()
        async with sem.guard() as ok:
            if not ok:
                return  # cooldown not satisfied
            full = await stream_letta(...)
            await push_to_group(full)

    The async context manager:

    1.  Acquires the inner ``asyncio.Semaphore(1)`` — only one NPC can be in
        here at any moment.  This serialises LLM calls.
    2.  Checks the cool-down: if less than 10s since last push, the block
        yields False (caller should bail out).  Otherwise it bumps
        ``last_push_at`` and yields True.
    3.  On exit, releases the semaphore so the next NPC can enter.

    The semaphore is lazy — primitives are created on first use from an async
    context, so importing this module from sync test fixtures doesn't fail.
    """

    def __init__(self, *, cooldown_seconds: float = _COOLDOWN_SECONDS) -> None:
        self._cooldown = float(cooldown_seconds)
        self._init_lock = threading.Lock()
        self._slot = _Slot()

    # ------------------------------------------------------------------
    # lazy loop-bound primitive init
    # ------------------------------------------------------------------
    def _ensure_loop(self) -> _Slot:
        """Build the semaphore + cooldown lock if not yet built (loop-bound)."""
        # Fast path: already initialised.
        if self._slot.semaphore is not None:
            return self._slot
        with self._init_lock:
            if self._slot.semaphore is None:
                self._slot.semaphore = asyncio.Semaphore(1)
                self._slot.cooldown_lock = asyncio.Lock()
        return self._slot

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------
    @property
    def cooldown_seconds(self) -> float:
        return self._cooldown

    def set_cooldown_seconds(self, seconds: float) -> None:
        """Live-tune the cool-down (used by tests)."""
        self._cooldown = max(0.0, float(seconds))

    @property
    def last_push_at(self) -> float:
        """Read the last push timestamp (epoch seconds). 0 = never."""
        return self._slot.last_push_at

    def is_in_cooldown(self, *, now: float | None = None) -> bool:
        """Return True iff we're still inside the cool-down window."""
        if self._cooldown <= 0:
            return False
        if self._slot.last_push_at <= 0:
            return False
        if now is None:
            now = time.time()
        return (now - self._slot.last_push_at) < self._cooldown

    def cooldown_remaining(self, *, now: float | None = None) -> float:
        """Return how many seconds until the cool-down expires (0 if already past)."""
        if self._slot.last_push_at <= 0 or self._cooldown <= 0:
            return 0.0
        if now is None:
            now = time.time()
        return max(0.0, self._cooldown - (now - self._slot.last_push_at))

    def guard(self):
        """Return an async context manager that wraps the entire push gate.

        Async usage::

            async with sem.guard() as ok:
                if not ok:
                    return  # skipped, still in cooldown
                ...  # do the push

        On ``__aenter__``:

        1. Acquires the inner semaphore (single-flight).
        2. Checks cool-down; if violated, releases the semaphore and yields
           ``False`` — caller must bail.

        On ``__aexit__``:
        - Releases the semaphore unconditionally.
        """
        return _SemaphoreGuard(self)


class _SemaphoreGuard:
    """Async context manager returned by ``GroupChatSemaphore.guard()``."""

    def __init__(self, parent: GroupChatSemaphore) -> None:
        self._parent = parent
        self._sem: asyncio.Semaphore | None = None
        self._ok: bool = False

    async def __aenter__(self) -> bool:
        slot = self._parent._ensure_loop()
        assert slot.semaphore is not None  # for mypy
        await slot.semaphore.acquire()
        self._sem = slot.semaphore
        # Cool-down gate — if violated, release the semaphore and report False.
        if self._parent.is_in_cooldown():
            slot.semaphore.release()
            self._sem = None
            self._ok = False
            return False
        # Stamp the push time and yield True.
        slot.last_push_at = time.time()
        self._ok = True
        return True

    async def __aexit__(self, exc_type, exc, tb) -> None:
        # Release if we still hold it (i.e. entered successfully).
        if self._sem is not None:
            self._sem.release()
            self._sem = None
        # If exception, optionally clear last_push_at so a retry can proceed.
        # (ADR-0007 §Out-of-scope: "no conversation persistence across loop
        # restarts" — we do NOT clear on exc; the next loop will see a fresh
        # cooldown naturally.)
        return None


# ============================================================================
# Module-level singleton (process-wide)
# ============================================================================
_semaphore: GroupChatSemaphore | None = None
_semaphore_lock = threading.Lock()


def get_group_semaphore() -> GroupChatSemaphore:
    """Return the process-wide ``GroupChatSemaphore`` (lazy init)."""
    global _semaphore
    with _semaphore_lock:
        if _semaphore is None:
            _semaphore = GroupChatSemaphore()
        return _semaphore


def set_group_semaphore(sem: GroupChatSemaphore | None) -> GroupChatSemaphore | None:
    """Replace (or clear) the singleton.  Returns the previous instance.

    Tests pass ``GroupChatSemaphore(cooldown_seconds=...)`` to get an isolated
    instance with a tighter cool-down so they don't have to wait 10s.
    """
    global _semaphore
    with _semaphore_lock:
        old = _semaphore
        _semaphore = sem
        return old


__all__ = [
    "GroupChatSemaphore",
    "get_group_semaphore",
    "set_group_semaphore",
]