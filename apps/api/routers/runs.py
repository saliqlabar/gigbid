"""Agent run viewer: node-by-node events (live over SSE), checkpoint history and replay from any node."""
from __future__ import annotations

import os

from fastapi import APIRouter, HTTPException, Request

from agent import trace
from apps.api.deps import CurrentUser, graphs
from apps.api.schemas import Checkpoint, RunEvent
from apps.api.streaming import stream_thread

router = APIRouter(prefix="/runs", tags=["runs"])


async def _owned(request: Request, thread_id: str, user: dict):
    bid, conv = graphs(request)
    g = conv if thread_id.startswith("conv-") else bid
    snap = await g.aget_state({"configurable": {"thread_id": thread_id}})
    if not snap.values or snap.values.get("user_id") != user["user_id"]:
        raise HTTPException(404, "run not found")
    return g, snap


@router.get("/{thread_id}/events", response_model=list[RunEvent])
async def events(thread_id: str, request: Request, after: float = 0.0, user: dict = CurrentUser):
    await _owned(request, thread_id, user)
    return [RunEvent(**{k: e[k] for k in ("ts", "node", "kind", "data", "trace_id")}) for e in trace.events(thread_id, after)]


@router.get("/{thread_id}/stream")
async def stream(thread_id: str, request: Request, after: float = 0.0, user: dict = CurrentUser):
    await _owned(request, thread_id, user)
    return await stream_thread(request, thread_id, after)


@router.get("/{thread_id}/trace")
async def trace_link(thread_id: str, request: Request, user: dict = CurrentUser):
    _, snap = await _owned(request, thread_id, user)
    tid = snap.values.get("trace_id")
    base = os.getenv("PHOENIX_PUBLIC_URL", "")
    return {"trace_id": tid, "url": f"{base}/projects/default/traces?filter=trace_id%3D%3D%27{tid}%27" if base else None}


@router.get("/{thread_id}/history", response_model=list[Checkpoint])
async def history(thread_id: str, request: Request, user: dict = CurrentUser):
    g, _ = await _owned(request, thread_id, user)
    out = []
    async for s in g.aget_state_history({"configurable": {"thread_id": thread_id}}):
        out.append(Checkpoint(checkpoint_id=s.config["configurable"]["checkpoint_id"], next=list(s.next),
                              step=(s.metadata or {}).get("step"), status=s.values.get("status"),
                              created_at=s.created_at))
    return out


@router.post("/{thread_id}/replay/{checkpoint_id}")
async def replay(thread_id: str, checkpoint_id: str, request: Request, user: dict = CurrentUser):
    """Replay a job from any checkpoint (LangGraph time travel): the nodes after that checkpoint run again
    on a new branch of the thread; history keeps the original branch. Writes stay safe because the
    submitter's idempotency keys are derived from the thread id: a replayed submit is a no-op."""
    from agent.runner import _drive
    g, snap = await _owned(request, thread_id, user)
    cfg = {"configurable": {"thread_id": thread_id, "checkpoint_id": checkpoint_id,
                            "trace_id": snap.values.get("trace_id")}, "recursion_limit": 60}
    res = await _drive(g, None, cfg, thread_id, user["user_id"])
    return {"thread_id": thread_id, "status": res["status"]}
