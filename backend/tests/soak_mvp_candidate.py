"""Real-time MVP Candidate soak through the FastAPI WebSocket protocol.

Uses the repository MockChatModel at zero chunk delay, so the run exercises
WebSocket routing, behavior assessment, deterministic arbitration, SQLite
memory/decision logs and idempotency without paid model traffic.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path


ROLE_MENTIONS = ["宋书航", "药师", "狂刀三浪", "北河散人", "白前辈", "灵蝶尊者"]
ROLE_KEYS = ["shu-hang", "yao-shi", "san-lang", "bei-he", "bai-qianbei", "ling-die"]
ROLE_MARKERS = {
    "shu-hang": "妈耶",
    "yao-shi": "老夫药师",
    "san-lang": "三浪就手痒",
    "bei-he": "老朽北河",
    "bai-qianbei": "嗯。善。",
    "ling-die": "妾身以为",
}


def _write_status(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_suffix(".tmp")
    pending.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    pending.replace(path)


def run(duration_seconds: float, interval_seconds: float, status_path: Path) -> int:
    run_token = int(time.time() * 1000)
    harness_dir = status_path.resolve().parent
    os.environ["USE_MOCK_LLM"] = "true"
    os.environ["USE_LETTA"] = "false"
    os.environ["GC_LOOPS_ENABLED"] = "true"
    os.environ["XZ_CRON_ENABLED"] = "false"
    # Deliberately small so a 24h run proves the proactive hard budget is
    # reached and remains closed, rather than merely checking its config.
    os.environ["GC_DAILY_BUDGET"] = "3"
    os.environ["AGENT_MEMORY_DB_PATH"] = str(harness_dir / f"soak_memory_{run_token}.sqlite")
    os.environ["BEHAVIOR_DECISION_DB_PATH"] = str(
        harness_dir / f"soak_decisions_{run_token}.sqlite"
    )

    from fastapi.testclient import TestClient

    from app import graph
    from app.behavior import CandidateIntent, IntentAssessment
    from app.config import get_settings
    from app.llm import MockChatModel
    import app.llm as llm_module
    from app.main import app

    get_settings.cache_clear()
    zero_delay_model = MockChatModel(chunk_delay_ms=0)
    graph.get_chat_model = lambda *args, **kwargs: zero_delay_model
    llm_module.get_chat_model = lambda *args, **kwargs: zero_delay_model

    started = time.time()
    deadline = started + duration_seconds
    counters = {
        "turns": 0,
        "agent_messages": 0,
        "silent_turns": 0,
        "two_responder_turns": 0,
        "role_responses": {role_key: 0 for role_key in ROLE_KEYS},
        "duplicate_checks": 0,
        "coordinator_checks": 0,
        "budget_checks": 0,
        "budget_probe_pushes": 0,
        "budget_exhaustion_checks": 0,
        "proactive_messages": 0,
        "chain_stop_checks": 0,
        "replay_checks": 0,
        "dm_checks": 0,
        "errors": 0,
        "violations": 0,
    }
    last_status_write = 0.0
    state = "running"
    failure: str | None = None

    try:
        with TestClient(app) as client:
            with client.websocket_connect("/ws/soak-session") as ws:
                init = ws.receive_json()
                if init.get("type") != "session_init":
                    raise AssertionError(f"expected session_init, got {init}")

                turn = 0
                while time.time() < deadline:
                    turn_started = time.time()
                    mention = ROLE_MENTIONS[turn % len(ROLE_MENTIONS)]
                    scenario = "ordinary"
                    text = f"@{mention} 第 {turn} 次稳定性问候"
                    original_assessor = graph.assess_intents_detailed
                    if turn % 10 == 0:
                        # Exercise a valid natural-silence decision through the
                        # real WS, log, memory and replay paths.
                        scenario = "silence"
                        text = f"第 {turn} 次无需回应的轻量状态。"

                        async def silence_assessment(event, context):
                            return IntentAssessment(candidates=[])

                        graph.assess_intents_detailed = silence_assessment
                    elif turn % 10 == 5:
                        # Exercise the upper bound itself, not merely the
                        # invariant that an ordinary mock turn happened to be <= 2.
                        scenario = "two_responders"
                        text = f"第 {turn} 次需要两个不同角度的回应。"
                        # Avoid the two immediately preceding mention targets,
                        # because production policy intentionally penalizes
                        # recent speakers and could correctly reduce this
                        # fixture to one responder.
                        recent_role_keys = {
                            ROLE_KEYS[(turn - 1) % len(ROLE_KEYS)],
                            ROLE_KEYS[(turn - 2) % len(ROLE_KEYS)],
                        }
                        fixture_roles = [
                            role_key for role_key in ROLE_KEYS if role_key not in recent_role_keys
                        ][:2]

                        async def two_responder_assessment(event, context):
                            def strong(role_key: str, contribution: str) -> CandidateIntent:
                                return CandidateIntent(
                                    role_key=role_key,
                                    relevance=3,
                                    social_obligation=3,
                                    relationship_motivation=3,
                                    continuity=3,
                                    persona_impulse=3,
                                    novelty_potential=3,
                                    proposed_action="reply",
                                    contribution_key=contribution,
                                    reason_codes=["soak_two_responder_fixture"],
                                )

                            return IntentAssessment(candidates=[
                                strong(fixture_roles[0], "first_distinct_angle"),
                                strong(fixture_roles[1], "second_distinct_angle"),
                            ])

                        graph.assess_intents_detailed = two_responder_assessment
                    event_id = f"soak-{int(started)}-{turn}"
                    packet = {
                        "type": "user_msg",
                        "payload": {"text": text, "msg_id": event_id, "author": "soak-user"},
                    }
                    rounds = 0
                    try:
                        ws.send_json(packet)
                        while True:
                            message = ws.receive_json()
                            msg_type = message.get("type")
                            if msg_type == "agent_done":
                                rounds += 1
                                counters["agent_messages"] += 1
                                payload = message.get("payload", {})
                                role_key = payload.get("agent")
                                full_text = payload.get("full_text", "")
                                marker = ROLE_MARKERS.get(role_key)
                                if marker is None or marker not in full_text:
                                    counters["violations"] += 1
                                    raise AssertionError(
                                        f"turn {turn}: identity mismatch for {role_key}: {full_text!r}"
                                    )
                                counters["role_responses"][role_key] += 1
                            elif msg_type == "error":
                                counters["errors"] += 1
                            elif msg_type == "cron_agent_post":
                                payload = message.get("payload", {})
                                role_key = payload.get("role_key")
                                full_text = payload.get("full_text", "")
                                marker = ROLE_MARKERS.get(role_key)
                                if marker is None or marker not in full_text:
                                    counters["violations"] += 1
                                    raise AssertionError(
                                        f"proactive identity mismatch for {role_key}: {full_text!r}"
                                    )
                                counters["proactive_messages"] += 1
                            elif msg_type == "group_chat_done":
                                break
                    finally:
                        graph.assess_intents_detailed = original_assessor
                    if rounds > 2:
                        counters["violations"] += 1
                        raise AssertionError(f"turn {turn}: {rounds} responders exceeds cap")
                    if rounds == 0:
                        counters["silent_turns"] += 1
                    if rounds == 2:
                        counters["two_responder_turns"] += 1
                    if scenario == "silence" and rounds != 0:
                        counters["violations"] += 1
                        raise AssertionError(f"turn {turn}: silence fixture produced {rounds} replies")
                    if scenario == "two_responders" and rounds != 2:
                        counters["violations"] += 1
                        raise AssertionError(f"turn {turn}: two-responder fixture produced {rounds} replies")

                    # Every tenth turn, resend the exact event and require no generation.
                    if turn % 10 == 0:
                        ws.send_json(packet)
                        duplicate_rounds = 0
                        while True:
                            message = ws.receive_json()
                            if message.get("type") == "agent_done":
                                duplicate_rounds += 1
                            elif message.get("type") == "cron_agent_post":
                                payload = message.get("payload", {})
                                role_key = payload.get("role_key")
                                full_text = payload.get("full_text", "")
                                marker = ROLE_MARKERS.get(role_key)
                                if marker is None or marker not in full_text:
                                    counters["violations"] += 1
                                    raise AssertionError(
                                        f"proactive identity mismatch for {role_key}: {full_text!r}"
                                    )
                                counters["proactive_messages"] += 1
                            if message.get("type") == "group_chat_done":
                                break
                        counters["duplicate_checks"] += 1
                        if duplicate_rounds:
                            counters["violations"] += 1
                            raise AssertionError("duplicate event generated an agent message")

                    # Periodically prove the new coordinator stays alive and
                    # the legacy random cron remains dormant.
                    if turn % 60 == 0:
                        scheduler_status = client.get("/api/cron/status")
                        scheduler_status.raise_for_status()
                        snapshot = scheduler_status.json()
                        if not snapshot["behavior_coordinator"]["running"]:
                            raise AssertionError("behavior coordinator task is not running")
                        if snapshot["xiuzhen"]["running"]:
                            raise AssertionError("legacy random cron unexpectedly started")
                        coordinator_snapshot = snapshot["behavior_coordinator"]
                        budget = coordinator_snapshot["daily_budget"]
                        daily_counts = coordinator_snapshot["daily_counts"]
                        if budget != 3 or any(count > budget for count in daily_counts.values()):
                            raise AssertionError(
                                f"proactive daily budget penetrated: budget={budget}, counts={daily_counts}"
                            )
                        counters["budget_checks"] += 1

                        # Explicitly drive one role until its production hard
                        # budget closes.  The first three attempts must push;
                        # all later attempts must remain silent.
                        budget_probe = client.post("/api/cron/trigger", json={
                            "service": "behavior",
                            "behavior_event_type": "idle_tick",
                            "text": "@白前辈 预算稳定性检查。",
                            "target": "system",
                        })
                        budget_probe.raise_for_status()
                        budget_result = budget_probe.json()
                        if counters["budget_probe_pushes"] < budget:
                            # Accelerated preflights can legitimately hit the
                            # 10-second group semaphore between probes.  The
                            # authoritative 1-second/turn run spaces probes by
                            # 60 seconds and therefore must push here.
                            if (
                                budget_result.get("event") == "skipped"
                                and budget_result.get("reason") == "group_cooldown"
                                and interval_seconds < 1.0
                            ):
                                pass
                            elif budget_result.get("event") != "pushed":
                                raise AssertionError(
                                    "budget probe should push before exhaustion, "
                                    f"got {budget_result}"
                                )
                            else:
                                counters["budget_probe_pushes"] += 1
                        elif budget_result.get("event") != "silent":
                            raise AssertionError(
                                f"budget should remain closed after exhaustion, got {budget_result}"
                            )
                        else:
                            counters["budget_exhaustion_checks"] += 1
                        if budget_result.get("event") == "pushed":
                            if (
                                budget_result.get("role_key") != "bai-qianbei"
                                or ROLE_MARKERS["bai-qianbei"] not in budget_result.get("text", "")
                            ):
                                raise AssertionError(f"budget probe identity mismatch: {budget_result}")
                        counters["coordinator_checks"] += 1

                    # Audit replay must stay deterministic after sustained
                    # SQLite writes.  The same cadence also injects a depth-3
                    # NPC event and requires the production coordinator to
                    # stop the chain without generating another message.
                    if turn % 100 == 0:
                        chain_stop = client.post("/api/cron/trigger", json={
                            "service": "behavior",
                            "behavior_event_type": "npc_message",
                            "text": "三跳链停止稳定性检查。",
                            "target": "san-lang",
                            "chain_depth": 3,
                        })
                        chain_stop.raise_for_status()
                        chain_result = chain_stop.json()
                        if (
                            chain_result.get("event") != "silent"
                            or chain_result.get("reason") != "max_chain_depth_reached"
                        ):
                            raise AssertionError(f"depth-3 chain was not stopped: {chain_result}")
                        counters["chain_stop_checks"] += 1

                        replay = client.post(f"/api/behavior/decisions/{event_id}/replay")
                        replay.raise_for_status()
                        if replay.json().get("matches") is not True:
                            raise AssertionError("behavior decision replay diverged")
                        counters["replay_checks"] += 1

                    # Exercise DM target routing and duplicate suppression on
                    # the same live application every five minutes.
                    if turn % 300 == 0:
                        dm_event_id = f"soak-dm-{int(started)}-{turn}"
                        with client.websocket_connect("/ws/soak-dm") as dm_ws:
                            if dm_ws.receive_json().get("type") != "session_init":
                                raise AssertionError("DM socket missing session_init")
                            dm_ws.send_json({
                                "type": "dm_init",
                                "payload": {"target_agent": "yao-shi"},
                            })
                            if dm_ws.receive_json().get("type") != "dm_init":
                                raise AssertionError("DM init handshake failed")
                            dm_packet = {
                                "type": "dm_msg",
                                "payload": {
                                    "text": f"第 {turn} 次 DM 检查",
                                    "msg_id": dm_event_id,
                                    "author": "soak-user",
                                },
                            }
                            dm_ws.send_json(dm_packet)
                            dm_done = 0
                            while True:
                                dm_message = dm_ws.receive_json()
                                if dm_message.get("type") == "dm_done":
                                    dm_done += 1
                                    break
                            if dm_done != 1:
                                raise AssertionError("DM target did not respond exactly once")
                            dm_ws.send_json(dm_packet)
                            duplicate_ack = dm_ws.receive_json()
                            if (
                                duplicate_ack.get("type") != "dm_msg_ack"
                                or duplicate_ack.get("payload", {}).get("status") != "duplicate"
                            ):
                                raise AssertionError("duplicate DM was not suppressed")
                        counters["dm_checks"] += 1

                    counters["turns"] += 1
                    turn += 1
                    now = time.time()
                    if now - last_status_write >= 30 or now >= deadline:
                        _write_status(status_path, {
                            "state": state,
                            "started_at": started,
                            "updated_at": now,
                            "elapsed_seconds": round(now - started, 3),
                            "target_seconds": duration_seconds,
                            **counters,
                        })
                        last_status_write = now
                    remaining = interval_seconds - (time.time() - turn_started)
                    if remaining > 0:
                        time.sleep(remaining)

                if counters["silent_turns"] == 0:
                    raise AssertionError("soak completed without exercising natural silence")
                if counters["two_responder_turns"] == 0:
                    raise AssertionError("soak completed without exercising two responders")
                missing_roles = [
                    role_key for role_key, count in counters["role_responses"].items() if count == 0
                ]
                if missing_roles:
                    raise AssertionError(f"soak completed without role identity coverage: {missing_roles}")
                if duration_seconds >= 300 and counters["budget_exhaustion_checks"] == 0:
                    raise AssertionError("soak completed without proving budget exhaustion")
                if counters["chain_stop_checks"] == 0:
                    raise AssertionError("soak completed without proving depth-3 chain stop")
    except Exception as exc:  # noqa: BLE001
        state = "failed"
        failure = f"{type(exc).__name__}: {exc}"
        counters["errors"] += 1

    finished = time.time()
    if failure is None:
        state = "passed"
    _write_status(status_path, {
        "state": state,
        "started_at": started,
        "updated_at": finished,
        "elapsed_seconds": round(finished - started, 3),
        "target_seconds": duration_seconds,
        "failure": failure,
        **counters,
    })
    return 0 if state == "passed" else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hours", type=float, default=24.0)
    parser.add_argument("--seconds", type=float, default=None)
    parser.add_argument("--interval", type=float, default=1.0)
    parser.add_argument(
        "--status",
        type=Path,
        default=Path(__file__).resolve().parents[2] / ".harness" / "mvp_soak_status.json",
    )
    args = parser.parse_args()
    duration = args.seconds if args.seconds is not None else args.hours * 3600
    if duration <= 0 or args.interval < 0:
        parser.error("duration must be > 0 and interval must be >= 0")
    return run(duration, args.interval, args.status)


if __name__ == "__main__":
    sys.exit(main())
