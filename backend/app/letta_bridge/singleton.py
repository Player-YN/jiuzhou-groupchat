"""Process-wide singleton LettaClient + helpers.

Mirrors the pattern from Project A's `bff.letta_bridge.letta_client`
singleton usage: one `LettaClient` per process, shared across coroutines
(httpx.AsyncClient is concurrency-safe).

The singleton reads base URL + password from `app.config.get_settings()`
on first access, so flipping `LETTA_BASE_URL` env var + restart re-points
the BFF at a different Letta server without code changes.

Tests can swap the underlying transport by calling
`LettaClient.set_test_transport(mock_transport)` BEFORE constructing
the singleton via `get_letta_client()`.
"""
from __future__ import annotations

import logging
import threading

from app.config import get_settings
from app.letta_bridge.letta_client import LettaClient

logger = logging.getLogger(__name__)

_client: LettaClient | None = None
_client_lock = threading.Lock()


def get_letta_client() -> LettaClient:
    """Return the process-wide singleton LettaClient.

    Constructs lazily on first call.  Subsequent calls return the same
    instance — callers that want a fresh transport should call
    `set_letta_client_for_tests(None)` between tests.
    """
    global _client
    with _client_lock:
        if _client is None:
            s = get_settings()
            _client = LettaClient(
                base_url=s.letta_base_url,
                password=s.letta_api_key,
            )
            logger.info(
                "LettaClient singleton initialized: base_url=%s", s.letta_base_url,
            )
        return _client


def set_letta_client_for_tests(client: LettaClient | None) -> LettaClient | None:
    """Replace (or clear) the process-wide LettaClient singleton.

    Returns the previous instance.  Tests should pass `None` to clear the
    singleton so a fresh one is constructed on the next `get_letta_client()`.
    Production code MUST NOT call this.
    """
    global _client
    with _client_lock:
        old = _client
        _client = client
        return old


async def aclose_letta_client() -> None:
    """Close the singleton's HTTP connection pool.  Safe to call repeatedly."""
    global _client
    with _client_lock:
        if _client is not None:
            await _client.aclose()
            _client = None


__all__ = [
    "aclose_letta_client",
    "get_letta_client",
    "set_letta_client_for_tests",
]