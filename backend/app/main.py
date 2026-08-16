"""FastAPI entry — P0 scaffold.

Run:
    cd backend && uvicorn app.main:app --reload
Health check:
    curl http://localhost:8000/health

Stage 7: lifespan bootstraps 6 Letta NPC agents (idempotent — create or
reuse, seed persona core-memory + archival passages).  Bootstrap failure
is logged but does NOT block startup; the BFF runs in degraded mode
and per-call Letta errors gracefully fall back to per-role provider.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.routers import (
    admin_config,
    admin_cron,
    behavior_audit,
    group_history,
    history_delete,
    ws,
)
from app.scheduler import shutdown_scheduler, start_scheduler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """BFF startup/shutdown hook.

    On startup:
      1) Read settings (loads .env via app.config.get_settings()).
      2) If `USE_LETTA=true` and not in mock mode, bootstrap 6 NPC
         agents via `LettaAgentRegistry.bootstrap_all(...)`.
      3) Log the outcome (created / reused / recovered / failed counts).

    On shutdown:
      - aclose the singleton LettaClient (best-effort).
    """
    s = get_settings()
    if s.use_letta and not s.use_mock_llm:
        try:
            from app.letta_bridge import (
                get_default_registry,
                get_letta_client,
            )

            client = get_letta_client()
            registry = get_default_registry()
            outcome = await registry.bootstrap_all(
                client=client, llm_model=s.letta_llm_model,
            )
            logger.info(
                "[lifespan] Letta bootstrap: created=%s reused=%s "
                "recovered=%s failed=%s",
                outcome.created, outcome.reused,
                outcome.recovered, outcome.failed,
            )
        except Exception as exc:  # noqa: BLE001 — never block startup
            logger.warning("[lifespan] Letta bootstrap failed (degraded): %s", exc)
    else:
        logger.info(
            "[lifespan] Letta bootstrap skipped (use_letta=%s, use_mock_llm=%s)",
            s.use_letta, s.use_mock_llm,
        )

    # Stage 8 Cron: start proactive scheduler (xiuzhen group + DM followup).
    # Failure here MUST NOT block startup — the BFF runs in degraded mode
    # without proactive behaviour but with normal chat intact.
    try:
        start_scheduler()
        logger.info("[lifespan] cron scheduler started")
    except Exception as exc:  # noqa: BLE001
        logger.warning("[lifespan] cron scheduler failed to start (degraded): %s", exc)

    yield

    # shutdown
    try:
        from app.letta_bridge import aclose_letta_client
        await aclose_letta_client()
    except Exception as exc:  # noqa: BLE001
        logger.warning("[lifespan] Letta client aclose failed: %s", exc)

    # Stage 8 Cron: stop scheduler cleanly
    try:
        await shutdown_scheduler()
        logger.info("[lifespan] cron scheduler stopped")
    except Exception as exc:  # noqa: BLE001
        logger.warning("[lifespan] cron scheduler stop failed: %s", exc)


app = FastAPI(
    title="Project B — Group Chat",
    version="0.1.0",
    description="Persistent social group chat with one human and six fictional AI characters.",
    lifespan=lifespan,
)

# CORS — allow frontend dev server on 3000
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register routes
app.include_router(ws.router, tags=["ws"])
app.include_router(admin_cron.router, tags=["cron"])
app.include_router(admin_config.router, tags=["admin-config"])
app.include_router(group_history.router, tags=["group-history"])
# T9 / Piece C: Clear 按钮配套的 history-delete endpoints
app.include_router(history_delete.router, tags=["history-delete"])
app.include_router(behavior_audit.router, tags=["behavior-audit"])


@app.get("/")
async def root() -> dict:
    return {
        "name": "groupchat-backend",
        "version": "0.1.0",
        "stage": "P0",
        "ws": "/ws/{session_id}",
    }


@app.get("/health")
async def health() -> dict:
    """Liveness check. Always returns 200 with status=ok.

    For deeper status (Letta reachability + NPC agent counts), see
    `/api/health` route below (Stage 7 added).
    """
    return {"status": "ok"}


@app.get("/api/health")
async def api_health() -> dict:
    """Deep health probe — Letta reachability + per-NPC agent_id list.

    Useful for the docker-compose health check and for ops dashboards.
    Always returns 200; check `letta.status` and `letta.agents` for
    actual readiness.
    """
    s = get_settings()
    out: dict = {
        "status": "ok",
        "use_letta": s.use_letta,
        "use_mock_llm": s.use_mock_llm,
        "letta": {"status": "down", "base_url": s.letta_base_url, "agents": []},
    }
    if not s.use_letta or s.use_mock_llm:
        out["letta"]["status"] = "skipped"
        return out

    try:
        from app.letta_bridge import get_default_registry, get_letta_client

        client = get_letta_client()
        info = await client.health()
        out["letta"]["status"] = "up"
        out["letta"]["info"] = info

        reg = get_default_registry()
        out["letta"]["agents"] = reg.list_all()
    except Exception as exc:  # noqa: BLE001
        out["letta"]["error"] = str(exc)
    return out
