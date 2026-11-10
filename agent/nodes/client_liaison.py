"""Client Liaison: reads new client messages, classifies intent, drafts a reply (with interview slots
when asked), checks it with the Compliance Guard and interrupts for approval before anything is sent.
Keeps the pipeline stage current (replied / interview / hired)."""
from __future__ import annotations

import hashlib

from langgraph.types import interrupt

from agent.handoffs import ApprovalDecision, MessageIntent, ReplyDraft
from agent.llm import structured
from agent.mcp_client import ToolLayer
from agent.nodes._base import node
from agent.nodes.compliance import check
from agent.safety.injection import delimit, detect

STAGE = {"interview_request": "interview", "hire": "hired", "question": "replied", "negotiation": "replied",
         "scam_signal": "replied", "other": "replied"}


def human_slot(iso: str) -> str:
    from datetime import datetime
    d = datetime.fromisoformat(iso)
    return d.strftime("%a %d %b, %H:%M") + f" (UTC{d.strftime('%z')[:3]})"


def _history(msgs: list[dict], n: int = 8) -> str:
    return "\n".join(f"{m['sender']}: {m['text']}" for m in msgs[-n:-1]) or "(no earlier messages)"


@node("read_messages")
async def read_messages(state: dict, tools: ToolLayer) -> dict:
    res = await tools.call("client_liaison", "marketplace.list_messages", {"thread_id": state["conversation_id"]})
    threads = res.get("threads", [])
    msgs = threads[0]["messages"] if threads else []
    job = state.get("job") or {}
    if not job and threads and threads[0].get("job_id"):
        job = await tools.call("client_liaison", "marketplace.get_job", {"job_id": threads[0]["job_id"]})
    return {"messages": msgs, "job": job, "status": "reading"}


@node("classify")
async def classify(state: dict, tools: ToolLayer) -> dict:
    last = [m for m in state["messages"] if m["sender"] == "client"][-1]
    intent = await structured("intent", "intent", {
        "job_title": state["job"].get("title", ""), "history": delimit(_history(state["messages"]), "conversation"),
        "message": delimit(last["text"], "client_message")}, MessageIntent)
    intent.injection_detected = intent.injection_detected or bool(detect(last["text"]))
    return {"intent": intent.model_dump(), "last_seen_message_id": last["message_id"],
            "pipeline_stage": STAGE.get(intent.intent, "replied")}


@node("offer_slots")
async def offer_slots(state: dict, tools: ToolLayer) -> dict:
    res = await tools.call("client_liaison", "workspace.get_free_slots", {"max_slots": 3})
    return {"slots": res.get("slots", [])}


@node("draft_reply")
async def draft_reply(state: dict, tools: ToolLayer) -> dict:
    last = [m for m in state["messages"] if m["sender"] == "client"][-1]
    prop = state.get("proposal") or {}
    slots = [human_slot(s["start"]) for s in state.get("slots", [])]
    r = await structured("client_liaison", "client_liaison", {
        "job_title": state["job"].get("title", ""), "proposal": (prop.get("cover_letter") or "")[:600],
        "intent": state["intent"]["intent"], "slots": ", ".join(slots) or "(none needed)",
        "evidence": "; ".join(prop.get("evidence_ids", [])) or "(use only what our proposal said)",
        "history": delimit(_history(state["messages"]), "conversation"),
        "message": delimit(last["text"], "client_message")}, ReplyDraft, temperature=0.3)
    if slots and not r.proposed_slots:
        r.proposed_slots = slots
    verdict = check(r.text, {"rules": {"max_cover_letter_words": 300}})
    r.text = verdict.cleaned_text
    return {"reply_draft": r.model_dump(), "compliance_verdict": verdict.model_dump(),
            "status": "blocked" if not verdict.allowed else "drafted"}


@node("approve_reply")
async def approve_reply(state: dict, tools: ToolLayer) -> dict:
    v = state.get("compliance_verdict") or {}
    payload = {"type": "message", "thread_id": state["thread_id"], "conversation_id": state["conversation_id"],
               "job": {"job_id": state["job"].get("job_id"), "title": state["job"].get("title")},
               "messages": state["messages"][-8:], "intent": state["intent"], "draft": state["reply_draft"],
               "slots": state.get("slots", []), "compliance": v,
               "dry_run_preview": {"thread_id": state["conversation_id"], "text": state["reply_draft"]["text"],
                                   "connects_cost": 0},
               "allowed_actions": ["approve", "edit", "reject"] if v.get("allowed", True) else ["edit", "reject"]}
    dec = ApprovalDecision.model_validate(interrupt(payload))
    rec = {"action": dec.action, "user_id": dec.user_id, "reason": dec.reason}
    if dec.action == "edit":
        text = (dec.edited_text or state["reply_draft"]["text"]).strip()
        verdict = check(text, {"rules": {"max_cover_letter_words": 300}})
        return {"approvals": [rec], "reply_draft": {**state["reply_draft"], "text": verdict.cleaned_text},
                "compliance_verdict": verdict.model_dump(), "status": "edited"}
    if dec.action == "approve" and not v.get("allowed", True):
        return {"approvals": [{**rec, "error": "blocked by compliance"}], "status": "blocked"}
    return {"approvals": [rec], "status": "approved" if dec.action == "approve" else "rejected"}


@node("send_reply")
async def send_reply(state: dict, tools: ToolLayer) -> dict:
    text = state["reply_draft"]["text"]
    key = f"{state['thread_id']}:{state['last_seen_message_id']}:" + hashlib.sha256(text.encode()).hexdigest()[:10]
    sent = await tools.call("liaison_sender", "marketplace.send_message",
                            {"thread_id": state["conversation_id"], "text": text, "idempotency_key": key})
    holds = []
    if state["intent"]["intent"] in ("interview_request", "hire") and state.get("slots"):
        s = state["slots"][0]
        holds.append(await tools.call("liaison_sender", "workspace.create_interview_hold",
                                      {"start": s["start"], "title": f"Interview: {state['job'].get('title', '')[:80]}",
                                       "idempotency_key": key + ":hold"}))
    return {"sent": [{**sent, "holds": holds}], "status": "sent"}
