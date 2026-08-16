"""T9 / Piece C: history-delete REST endpoints.

DELETE /api/group/history?session_id=<sid>
    - 删 agent_memory 表里 (session_id=X, source='group') 所有 rows
    - 跨 6 角色 (group fan-out 落 shu-hang/yao-shi/.../ling-die 都一起清)
    - 不影响其他 session

DELETE /api/dm/history?session_id=<sid>&agent_key=<target>
    - 删 (session_id=X, agent_key=target, source='dm') 所有 rows
    - 不传 agent_key → 清空该 session 下**所有 DM 行** (跨所有 target)
    - 跨 session isolation: 不会误删其他 sid 的行

Response shape: {"deleted": <int>, "session_id": "<sid>", ...}

Cross-session isolation test:
    - 同一 session 不同 source 互不影响 (group 删 ≠ dm 删)
    - 不同 session 互不影响 (sid_a DELETE 不动 sid_b)

Frontend Clear 按钮 usage:
    1) confirm("确定清除本群聊窗口的所有消息吗?")
    2) DELETE /api/group/history?session_id=${sid}
    3) backend 返 {deleted: N}
    4) frontend 清 messages state + 新建 sid (forceNewSession) 重新拉 history
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query, status

from app.memory import get_agent_memory_store

router = APIRouter(prefix="/api", tags=["history-delete"])


@router.delete("/group/history")
async def delete_group_history(
    session_id: str = Query(..., min_length=1, description="WebSocket session id"),
) -> dict[str, Any]:
    """删除群聊窗口的历史 (Stage 8+ T9 Clear 按钮配套 endpoint)。

    只删 (session_id=X, source='group') rows — 不会动 DM 行的内存 (避免误删私信)。
    跨 6 角色同时清 (group fan-out 写过 6 份, 这里也对应 6 份一起删)。

    Args:
        session_id: WS session id

    Returns:
        {"deleted": N, "session_id": "<sid>", "scope": "group"}

    Raises:
        422: session_id 为空 / 缺失 (FastAPI Query 校验)
    """
    if not session_id or not session_id.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="session_id must be non-empty",
        )

    store = get_agent_memory_store()
    deleted = store.delete_session(session_id=session_id, source="group")
    return {
        "deleted": deleted,
        "session_id": session_id,
        "scope": "group",
    }


@router.delete("/dm/history")
async def delete_dm_history(
    session_id: str = Query(..., min_length=1, description="WebSocket session id"),
    agent_key: Optional[str] = Query(
        None,
        description=(
            "DM target agent (shu-hang / yao-shi / san-lang / bei-he / "
            "bai-qianbei / ling-die). 不传 = 清空该 session 下所有 DM 行。"
        ),
    ),
) -> dict[str, Any]:
    """删除私信窗口的历史 (Stage 8+ T9 Clear 按钮配套 endpoint)。

    只删 (session_id=X, source='dm') rows — 不会动群聊行。
    不传 agent_key = 清空该 session 下所有 DM 行 (跨所有 target 同时清);
    传 agent_key = 只清这个 target 的 DM 行 (其他 target 的私信保留)。

    Args:
        session_id: WS session id
        agent_key: 可选 DM target; None = 清所有 DM target

    Returns:
        {"deleted": N, "session_id": "<sid>", "agent_key": "<target or null>",
         "scope": "dm"}

    Raises:
        422: session_id 为空 / 缺失 (FastAPI Query 校验)
    """
    if not session_id or not session_id.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="session_id must be non-empty",
        )

    # Trim agent_key if provided; empty string → None (清所有)
    if agent_key is not None:
        agent_key = agent_key.strip() or None

    store = get_agent_memory_store()
    deleted = store.delete_session(
        session_id=session_id,
        agent_key=agent_key,
        source="dm",
    )
    return {
        "deleted": deleted,
        "session_id": session_id,
        "agent_key": agent_key,
        "scope": "dm",
    }