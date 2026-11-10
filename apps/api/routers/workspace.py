"""Pipeline board, client messages, analytics, settings, memory viewer, marketplace status, portfolio upload."""
from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile

from agent import db
from agent.mcp_client import MarketplaceDown, ToolCallError
from agent.settings import ROOT
from apps.api.auth.users import update_user
from apps.api.deps import CurrentUser, enqueue, graphs, tools_for, trace_id
from apps.api.routers.auth import me_of
from apps.api.routers.jobs import threads_for
from apps.api.schemas import Analytics, ConversationOut, Me, MemoryItem, PipelineCard, Settings, StatusOut

router = APIRouter(tags=["workspace"])
STAGES = {"draft": "drafted", "submitted": "submitted", "replied": "replied", "interview": "interview",
          "hired": "hired", "lost": "lost"}


async def _call(user, request, tool, args=None):
    try:
        return await tools_for(user, trace_id(request)).call("api", tool, args or {})
    except MarketplaceDown as e:
        raise HTTPException(503, f"marketplace unavailable: {e}") from e
    except ToolCallError as e:
        raise HTTPException(502, str(e)) from e


@router.get("/status", response_model=StatusOut)
async def status(request: Request, user: dict = CurrentUser):
    try:
        st = await tools_for(user, trace_id(request)).call("api", "marketplace.marketplace_status", {})
    except (MarketplaceDown, ToolCallError) as e:
        st = {"marketplace": "gigsim", "available": False, "detail": str(e)}
    degraded = sum(1 for r in threads_for(user["user_id"]) if r["status"] == "degraded")
    return StatusOut(marketplace=st.get("marketplace", "gigsim"), marketplace_available=bool(st.get("available")),
                     detail=st.get("detail", ""), queued_degraded=degraded,
                     worker_queue=request.app.state.queue is not None)


@router.get("/pipeline", response_model=list[PipelineCard])
async def pipeline(request: Request, user: dict = CurrentUser):
    threads = {r["job_id"]: r for r in threads_for(user["user_id"])}
    cards = [PipelineCard(thread_id=r["thread_id"], job_id=r["job_id"], title=r["title"], stage="drafted",
                          bid_amount=r["summary"].get("price"), connects=r["summary"].get("connects"), updated=r["updated"])
             for r in threads.values() if r["status"] in ("awaiting_approval", "blocked")]
    props = (await _call(user, request, "marketplace.list_proposals"))["proposals"]
    events = (await _call(user, request, "marketplace.list_events", {"after": 0}))["events"]
    viewed = {e["data"].get("proposal_id") for e in events if e["kind"] == "proposal_viewed"}
    for p in props:
        if p["status"] == "draft":
            continue
        stage = STAGES.get(p["status"], "submitted")
        if stage == "submitted" and p["proposal_id"] in viewed:
            stage = "viewed"
        t = threads.get(p["job_id"])
        cards.append(PipelineCard(proposal_id=p["proposal_id"], thread_id=t["thread_id"] if t else None, job_id=p["job_id"],
                                  title=t["title"] if t else None, stage=stage, bid_amount=p["data"].get("bid_amount"),
                                  updated=p["updated_at"]))
    return cards


@router.get("/messages", response_model=list[ConversationOut])
async def messages(request: Request, user: dict = CurrentUser):
    from apps.api.routers.approvals import pending_payload
    _, conv = graphs(request)
    threads = (await _call(user, request, "marketplace.list_messages"))["threads"]
    titles = {r["job_id"]: r["title"] for r in threads_for(user["user_id"])}
    out = []
    for t in threads:
        tid = f"conv-{user['user_id']}-{t['thread_id']}"
        snap = await conv.aget_state({"configurable": {"thread_id": tid}})
        pending = await pending_payload(conv, tid, user["user_id"]) if snap.values else None
        out.append(ConversationOut(conversation_id=t["thread_id"], job_id=t.get("job_id"), title=titles.get(t.get("job_id")),
                                   messages=t["messages"], thread_id=tid, pending=pending,
                                   stage=(snap.values or {}).get("pipeline_stage")))
    return out


@router.post("/messages/{conversation_id}/draft")
async def draft_reply(conversation_id: str, request: Request, user: dict = CurrentUser):
    await enqueue(request, "handle_conversation", user["user_id"], conversation_id, job_id=f"conv-{conversation_id}-manual")
    return {"queued": True, "thread_id": f"conv-{user['user_id']}-{conversation_id}"}


@router.get("/analytics", response_model=Analytics)
async def analytics(request: Request, user: dict = CurrentUser):
    threads = threads_for(user["user_id"])
    props = (await _call(user, request, "marketplace.list_proposals"))["proposals"]
    cat_of = {r["job_id"]: r["summary"].get("category") or "unknown" for r in threads}
    by = defaultdict(lambda: Counter())
    for p in props:
        if p["status"] == "draft":
            continue
        c = by[cat_of.get(p["job_id"], "unknown")]
        c["submitted"] += 1
        c[p["status"]] += 1
    by_category = [{"category": k, "submitted": v["submitted"],
                    "reply_rate": round((v["replied"] + v["interview"] + v["hired"]) / v["submitted"], 3) if v["submitted"] else None,
                    "hire_rate": round(v["hired"] / v["submitted"], 3) if v["submitted"] else None} for k, v in by.items()]
    spent = sum(r["summary"].get("connects") or 0 for r in threads if r["status"] == "submitted")
    wins = sum(1 for p in props if p["status"] in ("replied", "interview", "hired"))
    costs = [r["summary"].get("cost_usd") or 0 for r in threads if r["status"] in ("submitted", "awaiting_approval")]
    from agent.memory.store import observations
    rej = Counter(o.get("reason", "").strip().lower()[:80] for o in observations(user["user_id"], "rejection"))
    lat = _latencies(user["user_id"])
    return Analytics(by_category=by_category,
                     connects={"spent": spent, "replies_or_better": wins,
                               "connects_per_reply": round(spent / wins, 1) if wins else None},
                     cost_latency={"proposals": len(costs), "avg_cost_usd": round(sum(costs) / len(costs), 5) if costs else 0,
                                   **lat},
                     rejection_reasons=[{"reason": k, "count": v} for k, v in rej.most_common(8)],
                     decisions=dict(Counter(r["summary"].get("action") or r["status"] for r in threads)))


def _latencies(user_id: str) -> dict:
    rows = db.query("select thread_id, min(ts) a, max(ts) b from run_events where thread_id like ? group by thread_id",
                    (f"bid-{user_id}-%",))
    xs = sorted(r["b"] - r["a"] for r in rows if r["b"] and r["a"])
    if not xs:
        return {"p50_latency_s": None, "p95_latency_s": None}
    return {"p50_latency_s": round(xs[len(xs) // 2], 1), "p95_latency_s": round(xs[min(len(xs) - 1, int(0.95 * len(xs)))], 1)}


@router.get("/settings", response_model=Me)
def get_settings(user: dict = CurrentUser):
    return me_of(user)


@router.put("/settings", response_model=Me)
def put_settings(body: Settings, user: dict = CurrentUser):
    if body.marketplace == "upwork_live":
        for s in body.saved_searches:          # polite live polling (docs/RUNBOOK.md)
            s.interval_min = max(s.interval_min, 60)
    return me_of(update_user(user["user_id"], settings=body.model_dump()))


@router.get("/memory", response_model=list[MemoryItem])
async def memory(request: Request, user: dict = CurrentUser):
    from agent.memory.store import list_memory
    return [MemoryItem(**m) for m in await list_memory(request.app.state.store, user["user_id"])]


@router.delete("/memory")
async def delete_memory_item(namespace: str, key: str, request: Request, user: dict = CurrentUser):
    from agent.memory.store import delete_memory
    try:
        await delete_memory(request.app.state.store, namespace.split("/"), key, user["user_id"])
    except PermissionError as e:
        raise HTTPException(403, str(e)) from e
    return {"deleted": True}


@router.post("/portfolio/upload")
async def upload(kind: str = Form(..., pattern="^(case|cv|proposal)$"), file: UploadFile = File(...),
                 outcome: str = Form("", pattern="^(|won|replied|ignored)$"), user: dict = CurrentUser):
    """Add work to the portfolio; the index updates incrementally (only new chunks are embedded)."""
    name = Path(file.filename or "upload").name
    if not name.lower().endswith((".md", ".txt", ".pdf")):
        raise HTTPException(422, "upload .md, .txt or .pdf")
    data = await file.read()
    if len(data) > 2_000_000:
        raise HTTPException(413, "max 2 MB")
    d = ROOT / "data" / "processed" / "uploads" / user["persona"] / kind
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_bytes(data)
    if kind == "proposal" and outcome:
        (d / (name + ".outcome")).write_text(outcome)
    from rag.retriever import get_corpus
    n = len(get_corpus(user["persona"], refresh=True).items)
    return {"stored": f"{kind}/{name}", "portfolio_items": n}
