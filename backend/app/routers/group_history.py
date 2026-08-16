"""T6 — Group chat history REST endpoint.

GET /api/group/history?session_id=<sid>&limit=<n>

Returns persisted group chat events for a given session from AgentMemoryStore.

Design rationale (Stage 7 fan-out):
- Group events fan-out to ALL 6 九洲一号群 NPC agent memories (shu-hang /
  yao-shi / san-lang / bei-he / bai-qianbei / ling-die) at the same timestamp
  with the same source='group' / speaker_key / text.
- Reading from ONE agent's memory (we pick `shu-hang`) therefore returns each
  group event exactly once — no client-side dedup needed.
- We filter to `source='group'` so DM messages from this session (which live
  on the *target* agent's memory only, not fan-out) don't bleed in.

Why REST instead of WS:
- Frontend ChatRoom needs history on mount, before the WS handshake completes.
- REST is the natural fit for a read-once-on-reload pattern (WeChat-like).
- The live message protocol is unchanged (no breaking change to ws.py).

Cross-session isolation is guaranteed by AgentMemoryStore's WHERE
`session_id = ?` clause — even if the caller passes a wrong or empty
session_id, they get [] (never another session's events).
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, status

from app.memory import get_agent_memory_store

router = APIRouter(prefix="/api/group", tags=["group-history"])

# Default + hard limits — keep memory bounded even for huge sessions.
_DEFAULT_LIMIT = 100
_MAX_LIMIT = 500

# We read from shu-hang's memory because group events fan-out to all 6 agents,
# and shu-hang is the canonical "any group session has a shu-hang entry" choice.
# If the session has zero events, we still get [] (no error).
_GROUP_READOUT_AGENT_KEY = "shu-hang"


@router.get("/history")
async def get_group_history(
    session_id: str = Query(..., min_length=1, description="WebSocket session id"),
    limit: int = Query(
        _DEFAULT_LIMIT,
        ge=1,
        le=_MAX_LIMIT,
        description=f"Max events to return (1..{_MAX_LIMIT}, default {_DEFAULT_LIMIT}). "
        "Returns the most recent N events in chronological (ASC) order.",
    ),
) -> dict[str, Any]:
    """Return persisted group chat events for a session.

    Response shape:
        {
            "session_id": "<sid>",
            "count": <int>,           # how many events returned
            "limit": <int>,           # echo of effective limit
            "history": [
                {
                    "agent_key": "<memory-owner key>",
                    "role": "user" | "agent",
                    "source": "group",
                    "speaker_key": "<actual speaker: user or 6 role keys>",
                    "text": "<message text>",
                    "timestamp": <ms>,
                    "agent_name": "<nullable, when speaker is an agent>",
                    "agent_emoji": "<nullable, when speaker is an agent>",
                    "author": "<T9 / Piece B: human署名，user-typed 行填, AI 行 null>",
                },
                ...
            ]
        }

    Empty session -> `history: []`, `count: 0`. Never 404 — empty list is the
    semantic answer for "no events yet" (consistent with REST conventions for
    read endpoints scoped by id).
    """
    if not session_id or not session_id.strip():
        # FastAPI's `min_length=1` already rejects empty strings, but be defensive.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="session_id must be non-empty",
        )

    store = get_agent_memory_store()
    # load_agent_memory returns ASC by (timestamp, id); filter to group source.
    all_entries = store.load_agent_memory(session_id, _GROUP_READOUT_AGENT_KEY)
    group_entries = [e for e in all_entries if e.source == "group"]

    # Take the most-recent N, then re-sort ASC for client display.
    if len(group_entries) > limit:
        group_entries = group_entries[-limit:]
    # (already ASC from load_agent_memory, but defensive in case future sort changes)
    group_entries.sort(key=lambda x: (x.timestamp, getattr(x, "id", 0) or 0))

    history = [
        {
            "agent_key": e.agent_key,
            "role": e.role,
            "source": e.source,
            "speaker_key": e.speaker_key,
            "text": e.text,
            "timestamp": e.timestamp,
            "agent_name": e.agent_name,
            "agent_emoji": e.agent_emoji,
            # T9 / Piece B: user-typed 行的署名 (默认 '神秘人' 已经在
            # agent_memory.append_message 内部 backfill).
            "author": e.author,
        }
        for e in group_entries
    ]

    return {
        "session_id": session_id,
        "count": len(history),
        "limit": limit,
        "history": history,
    }