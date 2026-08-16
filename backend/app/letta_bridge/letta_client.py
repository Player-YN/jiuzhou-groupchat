"""Letta v0.16.8 HTTP client wrapper.

Reused pattern from Project A `bff/letta_bridge/letta_client.py`, adapted for
Project B's 九洲一号群聊天群 (6 NPC roles).

Why a thin wrapper rather than the official `letta-client` SDK?
- The SDK pins a specific httpx/letta version combo that drifted against the
  pinned v0.16.8 server we use (see `AGENTS.md` notes on Letta risk).
- In Stage 7 we only need a handful of endpoints — keeping the surface manual
  lets us adjust in one place when the Letta API drifts.

Endpoints used (all relative to `LETTA_BASE_URL`):
    GET  /v1/health                                      (no auth)
    GET  /v1/agents                                      (list)
    POST /v1/agents                                      (create)
    GET  /v1/agents/{id}                                 (read)
    POST /v1/agents/{id}/messages/stream                 (SSE stream — yields
                                                          one JSON dict per
                                                          `data:` event)

Auth model (Letta v0.16.x):
    HTTP Basic with username `letta` and password `LETTA_API_KEY`
    (default `letta_dev_password`). Matches the docker compose env in
    Project A and our own `docker-compose.yml` Letta service.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, AsyncIterator

import httpx

logger = logging.getLogger(__name__)


class LettaError(RuntimeError):
    """Raised for any non-2xx Letta response."""


class LettaClient:
    """Async HTTP client for the Letta v0.16.x REST API.

    Instances are cheap to construct; one per process is fine.  The same
    instance is safe to share across coroutines (httpx.AsyncClient is
    concurrency-safe).

    Tests can inject a mock transport via the class-level hook
    `LettaClient.set_test_transport(...)` to avoid hitting a real Letta
    server.
    """

    def __init__(
        self,
        base_url: str,
        password: str,
        timeout: float = 60.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._auth = httpx.BasicAuth("letta", password)
        self._timeout = timeout
        # Tests inject a mock transport via the class-level hook
        # `LettaClient.set_test_transport(...)`.  We pass it into the
        # AsyncClient constructor because httpx 0.28.x ignores later
        # `_transport` assignment.
        test_transport = getattr(type(self), "_test_transport", None)
        self._client = httpx.AsyncClient(
            auth=self._auth,
            base_url=self._base_url,
            timeout=timeout,
            transport=test_transport,
            follow_redirects=True,  # Letta 0.16.x+ /v1/* 307 redirect
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    # ------------------------------------------------------------------
    # Test hook
    # ------------------------------------------------------------------
    @classmethod
    def set_test_transport(cls, transport: httpx.AsyncBaseTransport | None) -> None:
        """Install (or clear) a process-wide mock transport used by every new
        LettaClient.

        Tests should call this in a fixture; passing `None` clears the
        override.  We use a class-level variable so the mock survives
        across `__init__` and is honored when the BFF lifespan
        instantiates a fresh client.
        """
        cls._test_transport = transport

    # ------------------------------------------------------------------
    # low-level
    # ------------------------------------------------------------------
    async def _get(self, path: str, **params: Any) -> Any:
        r = await self._client.get(path, params=params)
        return self._check(r)

    async def _post(self, path: str, json_body: dict[str, Any] | None = None) -> Any:
        r = await self._client.post(path, json=json_body or {})
        return self._check(r)

    @staticmethod
    def _check(r: httpx.Response) -> Any:
        if r.status_code >= 400:
            raise LettaError(
                f"letta {r.request.method} {r.request.url} -> {r.status_code}: {r.text[:300]}"
            )
        if not r.content:
            return None
        try:
            return r.json()
        except json.JSONDecodeError:
            return r.text

    # ------------------------------------------------------------------
    # high-level
    # ------------------------------------------------------------------
    async def health(self) -> dict[str, Any]:
        """`GET /v1/health` (unauthenticated).

        Reuses the same transport as the authed client so that tests using
        `MockTransport` exercise this path too (otherwise a fresh client
        would bypass the mock and try to hit the real Letta host).
        """
        transport = self._client._transport  # type: ignore[attr-defined]
        async with httpx.AsyncClient(
            base_url=self._base_url,
            timeout=10.0,
            transport=transport,
            follow_redirects=True,
        ) as c:
            r = await c.get("/v1/health")
            if r.status_code >= 400:
                raise LettaError(f"letta health -> {r.status_code}")
            try:
                return r.json()  # type: ignore[return-value]
            except (ValueError, json.JSONDecodeError):  # type: ignore[misc]
                raise LettaError("letta /v1/health returned non-JSON body")

    async def list_agents(self) -> list[dict[str, Any]]:
        return await self._get("/v1/agents")  # type: ignore[return-value]

    async def create_agent(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._post("/v1/agents", payload)  # type: ignore[return-value]

    async def get_agent(self, agent_id: str) -> dict[str, Any]:
        return await self._get(f"/v1/agents/{agent_id}")  # type: ignore[return-value]

    async def get_core_memory(self, agent_id: str) -> list[dict[str, Any]]:
        """Return core memory blocks for an agent.

        Letta 0.16.8 returns either:
          - {"agent_type": ..., "blocks": [...]} (newest)
          - {"core_memory": [...]}                (older)
          - bare list                              (very old)
        We accept any of the three for forward compatibility.
        """
        data = await self._get(f"/v1/agents/{agent_id}/core-memory")
        if data is None:
            return []
        if isinstance(data, dict):
            for key in ("blocks", "core_memory"):
                if key in data:
                    inner = data[key]
                    return list(inner) if isinstance(inner, list) else []
        if isinstance(data, list):
            return data
        raise LettaError(
            f"unexpected /core-memory payload type: {type(data).__name__}"
        )

    # ------------------------------------------------------------------
    # archival memory — used by the bootstrap path to seed NPC passages
    # ------------------------------------------------------------------
    async def list_archival_memory(
        self,
        agent_id: str,
        *,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """Return all archival-memory passages for `agent_id`.

        Accepts the three observed payload shapes:
            - bare list `[{id, text}, ...]`
            - `{"passages": [...]}` / `{"results": [...]}` /
              `{"archival_memory": [...]}`
        """
        params: dict[str, Any] = {}
        if limit is not None:
            params["limit"] = int(limit)
        data = await self._get(
            f"/v1/agents/{agent_id}/archival-memory", **params,
        )
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            for key in ("passages", "results", "archival_memory"):
                inner = data.get(key)
                if isinstance(inner, list):
                    return inner
        raise LettaError(
            f"unexpected /archival-memory payload type: {type(data).__name__}"
        )

    async def insert_archival_memory(
        self,
        agent_id: str,
        text: str,
    ) -> list[str]:
        """Insert one passage into `agent_id`'s archival memory.

        Returns:
            list of inserted passage ids.  Empty list is OK (some Letta
            builds return `{"result": "OK"}` without ids).

        Raises:
            LettaError on non-2xx or empty-text payload.
        """
        if not text:
            raise LettaError("archival_memory insert called with empty text")
        data = await self._post(
            f"/v1/agents/{agent_id}/archival-memory",
            {"text": text},
        )
        # Schema variants observed:
        #   - {"result": "OK", "ids": [str, ...]}
        #   - {"passages": [{"id": str, ...}, ...]}
        #   - bare list of ids or passages
        #   - {"id": str, ...}  (single)
        if isinstance(data, list):
            if data and isinstance(data[0], str):
                return data
            return [str(p.get("id")) for p in data if isinstance(p, dict) and p.get("id")]
        if isinstance(data, dict):
            ids = data.get("ids")
            if isinstance(ids, list):
                return [str(i) for i in ids]
            if "id" in data:
                return [str(data["id"])]
            passages = data.get("passages")
            if isinstance(passages, list):
                return [
                    str(p.get("id"))
                    for p in passages
                    if isinstance(p, dict) and p.get("id")
                ]
            if data.get("result") == "OK":
                return []
        raise LettaError(
            f"unexpected /archival-memory POST response: {data!r}"
        )

    # ------------------------------------------------------------------
    # streaming chat — primary path used by graph.py leaves
    # ------------------------------------------------------------------
    async def stream_message(
        self,
        agent_id: str,
        text: str,
    ) -> AsyncIterator[dict[str, Any]]:
        """Yield decoded SSE events from Letta's stream endpoint.

        Letta responds with `text/event-stream` body of JSON objects.  Each
        object has at minimum `message_type`.  We yield the entire dict so
        callers can decide what to forward.

        SSE parsing notes:
        - Events are delimited by blank lines (`\\n\\n` or `\\r\\n\\r\\n`).
        - Multiple `data:` lines in one event are joined with `\\n` before
          JSON decoding (standard SSE).
        - Letta also emits `[DONE]` sentinels — we skip them.
        - Comments (lines starting with `:`) are ignored.

        Implementation choice: we read the full body before yielding, just
        like in Project A.  This trades true byte-by-byte streaming for
        robust parsing (httpx's `aiter_lines` / `aiter_raw` had edge-case
        failures in earlier stages).  Per-turn bodies are <50 KB on a local
        Letta; latency budget is acceptable for our chat UX.
        """
        url = f"/v1/agents/{agent_id}/messages/stream"
        # Letta 0.16.8 expects {"messages": [{"role": "user", "content": "..."}]}
        # (NOT "text").  422 fires otherwise.
        stream_timeout = httpx.Timeout(
            connect=10.0,
            read=600.0,  # local Ollama cold-start + Letta round-trip can be slow
            write=10.0,
            pool=10.0,
        )
        async with httpx.AsyncClient(
            auth=self._auth,
            base_url=self._base_url,
            timeout=stream_timeout,
            transport=self._client._transport,  # type: ignore[attr-defined]
            follow_redirects=True,
        ) as stream_client:
            response = await stream_client.post(
                url,
                json={"messages": [{"role": "user", "content": text}]},
            )
            if response.status_code >= 400:
                raise LettaError(
                    f"letta stream -> {response.status_code}: {response.content[:300]!r}"
                )
            body = response.content
            await response.aclose()

        # ---- SSE parser over the full body --------------------------------
        text_blob = body.decode("utf-8", errors="replace")
        events: list[dict[str, Any]] = []
        for raw_event in re.split(r"\r?\n\r?\n", text_blob):
            if not raw_event.strip():
                continue
            data_parts: list[str] = []
            for line in raw_event.split("\n"):
                if line.endswith("\r"):
                    line = line[:-1]
                if not line or line.startswith(":"):
                    continue
                field, sep, value = line.partition(":")
                if not sep or field != "data":
                    continue
                if value.startswith(" "):
                    value = value[1:]
                data_parts.append(value)
            if not data_parts:
                continue
            payload = "\n".join(data_parts).strip()
            if not payload or payload == "[DONE]":
                continue
            try:
                events.append(json.loads(payload))
            except json.JSONDecodeError:
                logger.warning("non-JSON SSE chunk dropped: %r", payload[:120])

        for ev in events:
            yield ev


__all__ = ["LettaClient", "LettaError"]