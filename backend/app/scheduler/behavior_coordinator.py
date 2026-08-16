"""Single event coordinator for proactive group behavior.

Unlike the legacy six independent NPC loops, one stimulus produces exactly one
batch semantic assessment covering all characters, followed by deterministic
arbitration.  Idle stimuli intentionally select at most one speaker.
"""
from __future__ import annotations

import asyncio
import logging
import os
import random
import time
import uuid
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from app.behavior import (
    BehaviorEngine,
    BehaviorEvent,
    CandidatePolicy,
    EventType,
    assess_intents_detailed,
    get_decision_log_store,
)
from app.graph import (
    ROLES,
    _cap_response_piece,
    _stream_with_generation_timeout,
    _use_letta_path,
)
from app.llm import get_chat_model
from app.memory import get_agent_memory_store
from app.models import make_msg
from app.scheduler.connection_registry import get_connection_registry
from app.scheduler.group_semaphore import get_group_semaphore
from app.scheduler.letta_retry import LettaRetryExhausted, stream_via_letta_with_retry
from app.scheduler.state import CronState, get_cron_state

logger = logging.getLogger(__name__)


class BehaviorCoordinator:
    """Own the proactive event loop and expose a manually triggerable cycle."""

    def __init__(
        self,
        *,
        state: CronState | None = None,
        interval_min_sec: float | None = None,
        interval_max_sec: float | None = None,
        daily_budget: int | None = None,
    ) -> None:
        self.state = state or get_cron_state()
        # Livelier idle: default 20–55s (was 45–120). Env: BEHAVIOR_IDLE_MIN/MAX_SEC
        env_min = float(os.environ.get("BEHAVIOR_IDLE_MIN_SEC", "20"))
        env_max = float(os.environ.get("BEHAVIOR_IDLE_MAX_SEC", "55"))
        imin = env_min if interval_min_sec is None else float(interval_min_sec)
        imax = env_max if interval_max_sec is None else float(interval_max_sec)
        self.interval_min_sec = max(1.0, imin)
        self.interval_max_sec = max(self.interval_min_sec, imax)
        configured_budget = int(os.environ.get("GC_DAILY_BUDGET", "60"))
        self.daily_budget = max(0, configured_budget if daily_budget is None else daily_budget)
        self.task: asyncio.Task | None = None
        self.stop_event = asyncio.Event()
        self.total_events = 0
        self.silent_events = 0
        self.pushed_events = 0
        self.last_error: str | None = None
        self._daily_key = time.strftime("%Y-%m-%d")
        self._daily_counts: dict[str, int] = {}

    def start(self) -> None:
        if self.task is not None and not self.task.done():
            return
        self.stop_event = asyncio.Event()
        self.task = asyncio.create_task(self._run(), name="group-behavior-coordinator")

    async def stop(self) -> None:
        self.stop_event.set()
        if self.task is not None:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
        self.task = None

    def is_running(self) -> bool:
        return self.task is not None and not self.task.done()

    def status_dict(self) -> dict[str, Any]:
        return {
            "running": self.is_running(),
            "total_events": self.total_events,
            "silent_events": self.silent_events,
            "pushed_events": self.pushed_events,
            "daily_budget": self.daily_budget,
            "daily_counts": dict(self._daily_counts),
            "last_error": self.last_error,
        }

    async def _run(self) -> None:
        while not self.stop_event.is_set():
            try:
                await asyncio.wait_for(
                    self.stop_event.wait(),
                    timeout=random.uniform(self.interval_min_sec, self.interval_max_sec),
                )
            except asyncio.TimeoutError:
                await self.trigger("idle_tick", text="群聊进入短暂空闲。")
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                self.last_error = f"{type(exc).__name__}: {exc}"
                logger.exception("[behavior] coordinator cycle failed")

    def _reset_daily_if_needed(self) -> None:
        key = time.strftime("%Y-%m-%d")
        if key != self._daily_key:
            self._daily_key = key
            self._daily_counts.clear()

    @staticmethod
    def _dedupe_recent(entries: list[Any]) -> list[Any]:
        # Group fan-out stores one physical row per audience member.  Collapse
        # those copies before putting context in a batch prompt.
        seen: set[tuple[int, str, str]] = set()
        result: list[Any] = []
        for entry in entries:
            key = (entry.timestamp, entry.speaker_key, entry.text)
            if key not in seen:
                seen.add(key)
                result.append(entry)
        return result

    async def trigger(
        self,
        event_type: EventType,
        *,
        text: str,
        speaker_key: str = "system",
        chain_depth: int = 0,
        event_id: str | None = None,
        force: bool = False,
    ) -> dict[str, Any]:
        self._reset_daily_if_needed()
        event = BehaviorEvent(
            event_id=event_id or str(uuid.uuid4()),
            session_id="group-global",
            event_type=event_type,
            text=text,
            speaker_key=speaker_key,
            chain_depth=chain_depth,
        )
        if not self.state.enabled and not force:
            return {"event": "skipped", "reason": "disabled", "event_id": event.event_id}
        registry = get_connection_registry()
        if not registry.active_sessions() and not force:
            return {
                "event": "skipped",
                "reason": "no_active_sessions",
                "event_id": event.event_id,
            }
        log_store = get_decision_log_store()
        existing = log_store.get(event.event_id)
        if existing is not None:
            if log_store.matches_event(event) is False:
                return {"event": "error", "reason": "event_id_collision", "event_id": event.event_id}
            return {"event": "duplicate", "event_id": event.event_id}

        store = get_agent_memory_store()
        recent = self._dedupe_recent(store.load_recent_group_events(limit=120))[:20]
        context = [
            {"speaker": item.speaker_key, "text": item.text[:500]}
            for item in reversed(recent)
        ]
        assessment = await assess_intents_detailed(event, context)
        intents = assessment.candidates
        now = time.time()
        policies = {
            role_key: CandidatePolicy(
                muted=(
                    self.state.npc_filter is not None
                    and role_key not in self.state.npc_filter
                ),
                cooldown_active=self.state.should_throttle(
                    role_key,
                    float(os.environ.get("BEHAVIOR_COOLDOWN_SEC", "25")),
                    now=now,
                ),
                daily_count=self._daily_counts.get(role_key, 0),
                daily_budget=self.daily_budget,
                recently_spoke=self.state.should_throttle(role_key, 180.0, now=now),
            )
            for role_key in ROLES
        }
        decision = BehaviorEngine().decide(
            event,
            intents,
            policies,
            max_responders=1,
            assessment=assessment.metadata,
        )
        if not log_store.save(decision):
            return {"event": "duplicate", "event_id": event.event_id}

        self.total_events += 1
        if not decision.selected_roles:
            self.silent_events += 1
            return {
                "event": "silent",
                "event_id": event.event_id,
                "reason": decision.reason,
            }

        role_key = decision.selected_roles[0]
        role = ROLES[role_key]
        selected_score = next(item for item in decision.candidates if item.role_key == role_key)
        context_text = "\n".join(f"{row['speaker']}: {row['text']}" for row in context) or "(暂无消息)"
        prompt = (
            f"群聊最近事件：\n{context_text}\n\n"
            f"当前触发：{text}\n"
            f"你的发言贡献方向：{selected_score.contribution_key or '自然回应'}。\n"
            "请以角色口吻发一条不超过80字的群聊消息，不解释决策过程。"
        )
        full_text = ""
        generation_messages = [
            SystemMessage(content=role["system"]),
            HumanMessage(content=prompt),
        ]
        try:
            if _use_letta_path(role_key):
                async for piece in _stream_with_generation_timeout(stream_via_letta_with_retry(
                    role_key=role_key,
                    session_id="group-global",
                    all_msgs=generation_messages,
                )):
                    piece, capped = _cap_response_piece(full_text, piece)
                    full_text += piece
                    if capped:
                        break
            else:
                model = get_chat_model(provider=role.get("provider"))
                async for chunk in _stream_with_generation_timeout(
                    model.astream(generation_messages)
                ):
                    piece = (
                        chunk.content
                        if isinstance(chunk.content, str)
                        else str(chunk.content or "")
                    )
                    piece, capped = _cap_response_piece(full_text, piece)
                    full_text += piece
                    if capped:
                        break
        except LettaRetryExhausted as exc:
            self.last_error = str(exc)
            return {"event": "error", "reason": "letta_retry_exhausted", "event_id": event.event_id}
        except Exception as exc:  # noqa: BLE001
            self.last_error = f"{type(exc).__name__}: {exc}"
            return {
                "event": "error",
                "reason": "generation_failed",
                "event_id": event.event_id,
                "error_code": type(exc).__name__,
            }

        if not full_text.strip():
            self.silent_events += 1
            return {"event": "silent", "reason": "empty_generation", "event_id": event.event_id}

        sem = get_group_semaphore()
        async with sem.guard() as ok:
            if not ok:
                return {"event": "skipped", "reason": "group_cooldown", "event_id": event.event_id}
            sessions = registry.active_sessions()
            pushed_sessions = 0
            for session_id in sessions:
                try:
                    store.fan_out_group_event(
                        session_id=session_id,
                        speaker_key=role_key,
                        role="agent",
                        text=full_text,
                        agent_name=role["name"],
                        agent_emoji=role["emoji"],
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning("[behavior] memory fan-out failed for %s: %s", session_id, exc)
                ws = registry.get(session_id)
                if ws is not None:
                    try:
                        await ws.send_json(make_msg(
                            "cron_agent_post",
                            session_id,
                            role_key=role_key,
                            name=role["name"],
                            emoji=role["emoji"],
                            full_text=full_text,
                            source="behavior_coordinator",
                            event_id=event.event_id,
                        ))
                        pushed_sessions += 1
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(
                            "[behavior] websocket push failed for %s: %s",
                            session_id,
                            exc,
                        )

        self._daily_counts[role_key] = self._daily_counts.get(role_key, 0) + 1
        self.state.record_group_fire(role_key, ok=True)
        self.pushed_events += 1
        return {
            "event": "pushed",
            "event_id": event.event_id,
            "role_key": role_key,
            "text": full_text,
            "sessions": pushed_sessions,
        }


_coordinator: BehaviorCoordinator | None = None


def get_behavior_coordinator() -> BehaviorCoordinator:
    global _coordinator
    if _coordinator is None:
        _coordinator = BehaviorCoordinator()
    return _coordinator


def set_behavior_coordinator(value: BehaviorCoordinator | None) -> None:
    global _coordinator
    _coordinator = value
