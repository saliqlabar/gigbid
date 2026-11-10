"""Pending approvals (proposals and messages): read the interrupt payload, resume the graph thread."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from agent.runner import resume
from apps.api.deps import CurrentUser, graphs
from apps.api.routers.jobs import threads_for
from apps.api.schemas import ApprovalIn, ApprovalOut, Pending, RegenerateIn, RegenerateOut

router = APIRouter(tags=["approvals"])


def _graph_for(request: Request, thread_id: str):
    bid, conv = graphs(request)
    return (conv, "conversation") if thread_id.startswith("conv-") else (bid, "bid")


async def pending_payload(graph, thread_id: str, user_id: str) -> dict | None:
    snap = await graph.aget_state({"configurable": {"thread_id": thread_id}})
    if not snap.values or snap.values.get("user_id") != user_id:
        raise HTTPException(404, "thread not found")
    ints = [i.value for t in snap.tasks for i in (t.interrupts or [])]
    return ints[0] if ints else None


@router.get("/approvals", response_model=list[Pending])
async def list_pending(request: Request, user: dict = CurrentUser):
    out = []
    for kind in ("bid", "conversation"):
        for r in threads_for(user["user_id"], kind):
            if r["status"] == "awaiting_approval":
                g, _ = _graph_for(request, r["thread_id"])
                out.append(Pending(thread_id=r["thread_id"], kind=kind, title=r["title"], status=r["status"],
                                   payload=await pending_payload(g, r["thread_id"], user["user_id"]),
                                   updated=r["updated"]))
    return sorted(out, key=lambda p: -p.updated)


@router.get("/approvals/{thread_id}", response_model=Pending)
async def get_pending(thread_id: str, request: Request, user: dict = CurrentUser):
    """Survives page refreshes and API restarts: the payload is read from the checkpointed interrupt."""
    g, kind = _graph_for(request, thread_id)
    snap = await g.aget_state({"configurable": {"thread_id": thread_id}})
    p = await pending_payload(g, thread_id, user["user_id"])
    return Pending(thread_id=thread_id, kind=kind, title=(snap.values.get("job") or {}).get("title"),
                   status="awaiting_approval" if p else snap.values.get("status", ""), payload=p, updated=0)


@router.post("/approvals/{thread_id}", response_model=ApprovalOut)
async def decide(thread_id: str, body: ApprovalIn, request: Request, user: dict = CurrentUser):
    g, kind = _graph_for(request, thread_id)
    if body.action == "reject" and not (body.reason or "").strip():
        raise HTTPException(422, "a reason is required to reject")
    try:
        r = await resume(g, thread_id, user["user_id"], body.model_dump(exclude_none=True), kind=kind)
    except (KeyError, PermissionError) as e:
        raise HTTPException(404, str(e)) from e
    except ValueError as e:
        raise HTTPException(409, str(e)) from e
    return ApprovalOut(thread_id=thread_id, status=r["status"], pending=r["interrupts"][0] if r["interrupts"] else None,
                       submission=r["state"].get("submission"), sent=r["state"].get("sent"))


@router.post("/proposals/{thread_id}/regenerate", response_model=RegenerateOut)
async def regenerate_paragraph(thread_id: str, body: RegenerateIn, request: Request, user: dict = CurrentUser):
    """Rewrite ONE paragraph with the writer model and the same evidence pack. The result goes back into
    the editor; it reaches the client only through edit -> Compliance -> approval."""
    from agent.llm import acomplete
    from agent.nodes.writer import evidence_pack, format_evidence
    g, _ = _graph_for(request, thread_id)
    snap = await g.aget_state({"configurable": {"thread_id": thread_id}})
    s = snap.values
    if not s or s.get("user_id") != user["user_id"]:
        raise HTTPException(404, "thread not found")
    paras = [p for p in s["proposal_draft"]["cover_letter"].split("\n\n") if p.strip()]
    if body.paragraph_index >= len(paras):
        raise HTTPException(422, "no such paragraph")
    new = await acomplete("writer", "regenerate_paragraph", {
        "paragraph": paras[body.paragraph_index], "letter": "\n\n".join(paras),
        "instruction": body.instruction or "make it more specific to the client", "evidence": format_evidence(evidence_pack(s))})
    paras[body.paragraph_index] = new.strip()
    return RegenerateOut(paragraph_index=body.paragraph_index, text=new.strip(), full_text="\n\n".join(paras))
