from __future__ import annotations

import httpx
import pytest

from app.behavior import (
    BehaviorEngine,
    BehaviorEvent,
    CandidateIntent,
    DecisionLogStore,
    set_decision_log_store,
)


@pytest.mark.asyncio
async def test_audit_get_list_and_replay_endpoints(tmp_path):
    from app.main import app

    store = DecisionLogStore(tmp_path / "audit.sqlite")
    previous = set_decision_log_store(store)
    event = BehaviorEvent(
        event_id="audit-1",
        session_id="audit-session",
        event_type="user_message",
        text="@药师 看看",
    )
    intent = CandidateIntent(
        role_key="yao-shi",
        relevance=3,
        social_obligation=3,
        proposed_action="reply",
        contribution_key="diagnosis",
    )
    store.save(BehaviorEngine().decide(event, [intent]))
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver",
        ) as client:
            fetched = await client.get("/api/behavior/decisions/audit-1")
            assert fetched.status_code == 200
            assert fetched.json()["selected_roles"] == ["yao-shi"]

            listed = await client.get(
                "/api/behavior/decisions", params={"session_id": "audit-session"},
            )
            assert listed.status_code == 200
            assert listed.json()["count"] == 1

            replayed = await client.post("/api/behavior/decisions/audit-1/replay")
            assert replayed.status_code == 200
            assert replayed.json()["matches"] is True
    finally:
        set_decision_log_store(previous)
        store.close()
