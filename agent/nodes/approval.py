"""Human approval gate (R4/C5) and the exactly-once submitter (C6).

`approval` calls interrupt() with everything the human needs: the draft, the evidence behind every
claim, the risk report, the price rationale and the marketplace's dry_run preview (exact payload + Connects
cost). The thread pauses in the checkpointer; the UI resumes it with an ApprovalDecision:
  approve -> submitter (approving the proposal also approves spending the Connects shown in the preview)
  edit    -> the edit is diffed, recorded as a style observation, and the draft goes back to Compliance,
             then to approval again (nothing edited is ever sent without being re-checked and re-approved)
  reject  -> reason recorded (analytics + memory observation), thread ends.
There is no auto-send at any autonomy level.

`submitter` derives idempotency keys from the thread id (and the exact text for the draft), so a crash
between "approved" and "submitted" followed by a resume submits exactly once and spends Connects once.
"""
from __future__ import annotations

import difflib
import hashlib
import re

from langgraph.types import interrupt

from agent.handoffs import ApprovalDecision
from agent.mcp_client import ToolLayer
from agent.nodes._base import node


def keys(state: dict) -> tuple[str, str]:
    d = state["proposal_draft"]
    h = hashlib.sha256((d["cover_letter"] + "|".join(a["answer"] for a in d.get("screening_answers", []))).encode()).hexdigest()[:12]
    tid = state["thread_id"].replace("/", "-")
    return f"{tid}:draft:{h}", f"{tid}:submit"


def client_text(text: str) -> str:
    """What the client receives: the draft without the [evidence_id] markers (those power the UI hover)."""
    from rag.verify import CITE
    return re.sub(r"[ \t]+([.,;:!?])", r"\1", CITE.sub("", text)).strip()


def _payload_args(state: dict, key: str, dry_run: bool) -> dict:
    d, p = state["proposal_draft"], state["price_quote"]
    return {"job_id": state["job"]["job_id"], "cover_letter": client_text(d["cover_letter"]), "bid_amount": p["amount"],
            "bid_type": p["bid_type"],
            "screening_answers": [client_text(a["answer"]) for a in d.get("screening_answers", [])],
            "idempotency_key": key, "dry_run": dry_run}


def claims_with_evidence(state: dict) -> list[dict]:
    ev = {e["evidence_id"]: e for e in state.get("evidence", [])}
    out = []
    for c in (state.get("verification") or {}).get("claims", []):
        out.append({**c, "evidence": [{"evidence_id": i, "title": ev.get(i, {}).get("title", i),
                                        "snippet": ev.get(i, {}).get("snippet", ""), "uri": ev.get(i, {}).get("uri")}
                                       for i in c["evidence_ids"]]})
    return out


@node("approval")
async def approval(state: dict, tools: ToolLayer) -> dict:
    kd, _ = keys(state)
    verdict = state.get("compliance_verdict") or {}
    preview = None
    if verdict.get("allowed", True):
        preview = await tools.call("approval", "marketplace.create_proposal_draft", _payload_args(state, kd, True))
    payload = {"type": "proposal", "thread_id": state["thread_id"], "job": {k: state["job"].get(k) for k in
               ("job_id", "title", "category", "budget_type", "budget_max", "hourly_low", "hourly_high")},
               "draft": state["proposal_draft"], "claims": claims_with_evidence(state),
               "risk_report": state.get("risk_report"), "fit_report": state.get("fit_report"),
               "price_quote": state.get("price_quote"), "critique": (state.get("critiques") or [None])[-1],
               "compliance": verdict, "dry_run_preview": preview,
               "allowed_actions": ["approve", "edit", "reject"] if verdict.get("allowed", True) else ["edit", "reject"]}
    raw = interrupt(payload)
    dec = ApprovalDecision.model_validate(raw)
    rec = {"action": dec.action, "user_id": dec.user_id, "reason": dec.reason}
    if dec.action == "approve" and not verdict.get("allowed", True):
        return {"approvals": [{**rec, "error": "blocked by compliance: edit or reject"}], "status": "blocked"}
    if dec.action == "approve":
        return {"approvals": [rec], "status": "approved"}
    if dec.action == "reject":
        from agent.memory.store import add_observation
        add_observation(state.get("user_id", "local"), "rejection", {"thread_id": state["thread_id"],
                        "reason": dec.reason or "", "job_id": state["job"]["job_id"]}, state["job"].get("category") or "")
        return {"approvals": [rec], "status": "rejected"}
    # edit
    from agent.memory.store import record_edit
    from agent.runtime import store
    d = dict(state["proposal_draft"])
    original = d["cover_letter"]
    edited = (dec.edited_text or original).strip()
    diff = "\n".join(difflib.unified_diff(original.splitlines(), edited.splitlines(), "draft", "edited", lineterm=""))
    promoted = await record_edit(store(), state.get("user_id", "local"), original, edited, state["thread_id"])
    d["cover_letter"] = edited
    if dec.edited_answers is not None:
        d["screening_answers"] = [a.model_dump() for a in dec.edited_answers]
    return {"approvals": [{**rec, "diff": diff, "promoted_style_rules": [p["key"] for p in promoted]}],
            "proposal_draft": d, "drafts": [{"stage": "human_edit", **d}], "status": "edited"}


def route_after_approval(state: dict) -> str:
    from agent.nodes._base import halted
    if halted(state):
        return "end"
    return {"approved": "submitter", "edited": "compliance", "blocked": "approval"}.get(state.get("status"), "end")


@node("submitter")
async def submitter(state: dict, tools: ToolLayer) -> dict:
    kd, ks = keys(state)
    draft = await tools.call("submitter", "marketplace.create_proposal_draft", _payload_args(state, kd, False))
    sub = await tools.call("submitter", "marketplace.submit_proposal",
                           {"proposal_id": draft["proposal_id"], "idempotency_key": ks})
    return {"submission": {"proposal_id": draft["proposal_id"], "draft_key": kd, "submit_key": ks, **sub},
            "status": "submitted" if sub.get("status") == "submitted" else sub.get("status", "submitted")}
