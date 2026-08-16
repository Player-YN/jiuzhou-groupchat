"""Read-only audit and deterministic replay endpoints for behavior decisions."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.behavior import get_decision_log_store

router = APIRouter(prefix="/api/behavior", tags=["behavior-audit"])


@router.get("/decisions/{event_id}")
async def get_decision(event_id: str) -> dict[str, Any]:
    decision = get_decision_log_store().get(event_id)
    if decision is None:
        raise HTTPException(status_code=404, detail="behavior decision not found")
    return decision.model_dump(mode="json")


@router.get("/decisions")
async def list_decisions(
    session_id: str = Query(min_length=1),
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, Any]:
    decisions = get_decision_log_store().list_session(session_id, limit=limit)
    return {
        "session_id": session_id,
        "count": len(decisions),
        "decisions": [item.model_dump(mode="json") for item in decisions],
    }


@router.post("/decisions/{event_id}/replay")
async def replay_decision(event_id: str) -> dict[str, Any]:
    matches, original, replayed = get_decision_log_store().replay(event_id)
    if original is None:
        raise HTTPException(status_code=404, detail="behavior decision not found")
    return {
        "event_id": event_id,
        "matches": matches,
        "policy_version": original.policy_version,
        "original": original.model_dump(mode="json"),
        "replayed": replayed.model_dump(mode="json") if replayed else None,
    }
