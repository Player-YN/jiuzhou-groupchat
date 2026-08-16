"""P0 Eval gate 端到端测试：WS 连通 + 单轮回复 + 流式 chunk。

跑法：后端已起在 :8000 → `python tests/test_smoke_e2e.py`
"""
from __future__ import annotations

import asyncio
import json
import sys
import time

import websockets


URL = "ws://127.0.0.1:8000/ws/p0-smoke"


async def main() -> int:
    chunks: list[str] = []
    events: list[dict] = []
    session_init_seen = False
    ack_seen = False
    thinking_seen = False
    done_seen = False
    full_text = ""

    print(f"[E2E] connecting to {URL}")
    async with websockets.connect(URL) as ws:
        # 1) session_init
        raw = await asyncio.wait_for(ws.recv(), timeout=5)
        m = json.loads(raw)
        events.append(m)
        session_init_seen = m.get("type") == "session_init"
        print(f"[E2E] session_init: {m}")

        # 2) 发 user_msg
        await ws.send(json.dumps({
            "type": "user_msg",
            "session_id": "p0-smoke",
            "payload": {"text": "你好", "msg_id": "test-1"},
        }))
        print("[E2E] sent user_msg at +0s")

        # 3) 收集事件
        start = time.time()
        while True:
            elapsed = time.time() - start
            if elapsed > 15:
                print(f"[E2E] TIMEOUT after {elapsed:.1f}s")
                break
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=12 - elapsed)
            except asyncio.TimeoutError:
                print("[E2E] no more events within 12s, stop")
                break
            m = json.loads(raw)
            events.append(m)
            t = m.get("type")
            if t == "user_msg_ack":
                ack_seen = True
                print(f"[E2E] +{time.time()-start:.2f}s user_msg_ack")
            elif t == "agent_thinking":
                thinking_seen = True
                print(f"[E2E] +{time.time()-start:.2f}s agent_thinking")
            elif t == "agent_msg_chunk":
                chunks.append(m.get("payload", {}).get("chunk", ""))
                print(f"[E2E] +{time.time()-start:.2f}s chunk[{len(chunks)}]: {m.get('payload',{}).get('chunk','')[:20]!r}")
            elif t == "agent_done":
                done_seen = True
                full_text = m.get("payload", {}).get("full_text", "")
                print(f"[E2E] +{time.time()-start:.2f}s agent_done, full_text len={len(full_text)}")
                break
            elif t == "error":
                print(f"[E2E] +{time.time()-start:.2f}s ERROR: {m.get('payload',{})}")
                break

    # ===== 报告 =====
    print("\n" + "=" * 60)
    print("P0 E2E Report")
    print("=" * 60)
    print(f"  G0-3 WS 连通（session_init）: {'✅' if session_init_seen else '❌'}")
    print(f"  G0-4 单轮回复（ack + done）:  {'✅' if (ack_seen and done_seen) else '❌'}")
    print(f"  G0-5 流式 chunk 数:           {len(chunks)} (要求 ≥ 3) {'✅' if len(chunks) >= 3 else '❌'}")
    print(f"  full_text len:                {len(full_text)}")
    print(f"  thinking 事件:                {'✅' if thinking_seen else '❌ (non-blocking)'}")
    print(f"  total events:                 {len(events)}")
    print(f"  event types seen:             {[e.get('type') for e in events]}")

    ok = session_init_seen and ack_seen and done_seen and len(chunks) >= 3
    print("=" * 60)
    print(f"  RESULT: {'PASS' if ok else 'FAIL'}")
    print("=" * 60)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
