"""Job inbox and job detail."""
from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Request

from agent import db
from apps.api.deps import CurrentUser, enqueue, graphs
from apps.api.schemas import JobCard, JobDetail

router = APIRouter(prefix="/jobs", tags=["jobs"])
RANK = {"awaiting_approval": 0, "drafting": 1, "analysing": 2, "watching": 3, "degraded": 4, "needs_attention": 4,
        "dead_letter": 5, "blocked": 1, "submitted": 6, "skipped": 7, "rejected": 8}


def threads_for(user_id: str, kind: str = "bid") -> list[dict]:
    from agent.runner import _init
    _init()
    rows = db.query("select * from threads where user_id=? and kind=?", (user_id, kind))
    for r in rows:
        r["summary"] = json.loads(r["summary"] or "{}")
    return rows


@router.get("", response_model=list[JobCard])
def inbox(status: str = "", min_fit: float = 0.0, q: str = "", hide_skipped: bool = False, user: dict = CurrentUser):
    out = []
    for r in threads_for(user["user_id"]):
        s = r["summary"]
        if status and r["status"] != status:
            continue
        if hide_skipped and r["status"] in ("skipped", "rejected"):
            continue
        if min_fit and (s.get("fit") or 0) < min_fit:
            continue
        if q and q.lower() not in (r["title"] or "").lower():
            continue
        out.append(JobCard(thread_id=r["thread_id"], job_id=r["job_id"], title=r["title"], status=r["status"],
                           updated=r["updated"], **{k: s.get(k) for k in JobCard.model_fields
                                                    if k in s and k not in ("thread_id", "job_id", "title", "status")}))
    # ranked by fit within status groups: things needing the human first
    return sorted(out, key=lambda c: (RANK.get(c.status, 5), -(c.fit or 0)))


@router.get("/{thread_id}", response_model=JobDetail)
async def detail(thread_id: str, request: Request, user: dict = CurrentUser):
    bid, _ = graphs(request)
    snap = await bid.aget_state({"configurable": {"thread_id": thread_id}})
    s = snap.values
    if not s or s.get("user_id") != user["user_id"]:
        raise HTTPException(404, "job not found")
    pending = any(t.interrupts for t in snap.tasks)
    return JobDetail(thread_id=thread_id, status="awaiting_approval" if pending else s.get("status", ""),
                     job=s.get("job", {}), job_spec=s.get("job_spec"), risk_report=s.get("risk_report"),
                     fit_report=s.get("fit_report"), evidence=s.get("evidence", []), decision=s.get("decision"),
                     price_quote=s.get("price_quote"), errors=s.get("errors", []),
                     usage=[{k: u[k] for k in ("node", "latency_s", "tokens", "cost_usd")} for u in s.get("usage", [])],
                     pending_approval=pending, agency_route=s.get("agency_route"))


@router.post("/scout")
async def scout_now(request: Request, user: dict = CurrentUser):
    """Run the user's saved searches now (normally on their schedule in the worker)."""
    await enqueue(request, "scout_user", user["user_id"], job_id=None)
    return {"queued": True}


@router.post("/{job_id}/analyse")
async def analyse(job_id: str, request: Request, user: dict = CurrentUser):
    from agent.runner import bid_thread_id
    tid = bid_thread_id(user["user_id"], job_id)
    await enqueue(request, "analyse_job", user["user_id"], job_id, job_id=tid)
    return {"queued": True, "thread_id": tid}


@router.post("/{thread_id}/skip")
async def skip(thread_id: str, request: Request, user: dict = CurrentUser):
    from agent.runner import index_thread
    bid, _ = graphs(request)
    cfg = {"configurable": {"thread_id": thread_id}}
    snap = await bid.aget_state(cfg)
    if not snap.values or snap.values.get("user_id") != user["user_id"]:
        raise HTTPException(404, "job not found")
    index_thread(thread_id, user["user_id"], "bid", snap.values, "skipped")
    return {"status": "skipped"}


@router.post("/{thread_id}/retry")
async def retry(thread_id: str, request: Request, user: dict = CurrentUser):
    """Re-queue a degraded / dead-lettered thread from its last checkpoint."""
    await enqueue(request, "continue_thread", user["user_id"], thread_id, job_id=f"retry-{thread_id}")
    return {"queued": True}
