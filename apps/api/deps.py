"""Shared API dependencies: current user, graphs, tool layer, worker queue."""
from __future__ import annotations

import os
import uuid

from fastapi import Cookie, Depends, Header, HTTPException, Request

from apps.api.auth.users import get_user, read_token

COOKIE = "gigbid_session"


def current_user(authorization: str = Header(default=""), gigbid_session: str = Cookie(default="")) -> dict:
    tok = authorization.removeprefix("Bearer ").strip() or gigbid_session
    uid = read_token(tok) if tok else None
    user = get_user(uid) if uid else None
    if not user:
        raise HTTPException(401, "not signed in")
    return user


def graphs(request: Request):
    return request.app.state.bid_graph, request.app.state.conv_graph


def trace_id(request: Request) -> str:
    """One trace id per UI request: from the browser's X-Trace-Id header or minted here, then passed to the
    graph, every MCP call (header) and the audit log."""
    return request.headers.get("x-trace-id") or "tr_" + uuid.uuid4().hex[:16]


def tools_for(user: dict, tid: str):
    from agent.mcp_client import ToolLayer
    return ToolLayer(user["user_id"], user["persona"], tid)


async def enqueue(request: Request, fn: str, *args, job_id: str | None = None):
    """Queue work for the worker (arq on Redis). `job_id` makes enqueueing idempotent."""
    pool = request.app.state.queue
    if pool is None:
        if os.getenv("WORKER_MODE") == "inline":           # tests only: run the task in-process
            from worker import tasks
            return await getattr(tasks, fn)({"inline": True, "app_state": request.app.state}, *args)
        raise HTTPException(503, "worker queue unavailable")
    return await pool.enqueue_job(fn, *args, _job_id=job_id)


CurrentUser = Depends(current_user)
