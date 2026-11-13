"""GigBid API (FastAPI). Run: uvicorn apps.api.main:app --port 8000

On startup: opens the durable checkpointer + store (Postgres in compose/deploy), compiles both graphs,
connects to the worker queue (arq/Redis), preloads the reviewer login and starts tracing.
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from agent.observability import setup_tracing
from agent.persistence import open_persistence
from agent.runner import new_conversation_graph, new_graph
from agent.settings import ROOT
from apps.api.auth.users import ensure_reviewer
from apps.api.routers import approvals, auth, jobs, runs, workspace


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_tracing("gigbid-api")
    ensure_reviewer()
    async with open_persistence() as (cp, store):
        app.state.store = store
        app.state.bid_graph = new_graph(cp, store)
        app.state.conv_graph = new_conversation_graph(cp, store)
        app.state.queue = None
        clock = None
        if os.getenv("GIGSIM_INPROCESS") == "1":
            from gigsim import app as gigsim_app
            gigsim_app.init_db()
        if os.getenv("WORKER_MODE") == "inprocess":
            from apps.api import inprocess_worker
            clock = inprocess_worker.start(app.state)
        if os.getenv("REDIS_URL") and os.getenv("WORKER_MODE") != "inline":
            from arq import create_pool
            from arq.connections import RedisSettings
            try:
                app.state.queue = await create_pool(RedisSettings.from_dsn(os.environ["REDIS_URL"]))
            except Exception:  # noqa: BLE001 - surfaced by /status as worker_queue=false
                app.state.queue = None
        yield
        if clock is not None:
            clock.cancel()
        if app.state.queue is not None:
            await app.state.queue.aclose()


app = FastAPI(title="GigBid API", version="1.0.0", lifespan=lifespan,
              description="Multi-agent freelancer copilot: inbox, analysis, grounded proposals, approvals.")
app.add_middleware(CORSMiddleware, allow_origins=os.getenv("CORS_ORIGINS", "http://localhost:5173").split(","),
                   allow_credentials=True, allow_methods=["*"], allow_headers=["*"])


@app.get("/api/health", tags=["meta"])
async def health():
    return {"status": "ok", "service": "gigbid-api"}


for r in (auth.router, jobs.router, approvals.router, runs.router, workspace.router):
    app.include_router(r, prefix="/api")

# Serve the built web app (apps/web/dist) from the same origin in production.
_dist = ROOT / "apps" / "web" / "dist"
if _dist.exists():
    app.mount("/assets", StaticFiles(directory=_dist / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    async def spa(path: str):
        f = _dist / path
        return FileResponse(f if path and f.is_file() else _dist / "index.html")
