"""Runs graph threads: start a job, stream progress, stop at interrupts, resume with a decision.

Used by the worker (production path), the API (approval resume) and the eval runners. The thread index
(app table `threads`) mirrors each thread's status so the UI can list inboxes and pending approvals
without scanning checkpoints.
"""
from __future__ import annotations

import json
import time
import uuid
from typing import Any

from langgraph.types import Command

from agent import db, trace
from agent.graphs.bid_graph import build_bid_graph
from agent.settings import ROOT

_SCHEMA = """create table if not exists threads(thread_id text primary key, user_id text, kind text, job_id text,
  title text, status text, summary text, updated real)"""
_ready = False


def _init():
    global _ready
    if not _ready:
        db.executescript(_SCHEMA)
        _ready = True


def profile_for(persona: str) -> dict:
    p = ROOT / "data" / "personas" / persona / "profile.json"
    return json.loads(p.read_text()) if p.exists() else {}


def bid_thread_id(user_id: str, job_id: str) -> str:
    return f"bid-{user_id}-{job_id}"


def summarise(state: dict) -> dict:
    """Small summary for the inbox / pipeline (no drafts, no evidence)."""
    fit, risk, price, dec = (state.get(k) or {} for k in ("fit_report", "risk_report", "price_quote", "decision"))
    return {"fit": fit.get("score"), "risk": risk.get("score"), "risk_level": risk.get("level"),
            "risk_flags": [f["kind"] for f in risk.get("flags", [])], "scam": risk.get("scam"),
            "injection": risk.get("injection_detected"), "action": dec.get("action"), "rule": dec.get("rule"),
            "reasons": dec.get("reasons"), "price": price.get("amount"), "bid_type": price.get("bid_type"),
            "connects": price.get("connects_cost") if price else state.get("job", {}).get("connects_cost"),
            "tokens": state.get("tokens"), "cost_usd": state.get("cost_usd"), "steps": state.get("steps"),
            "errors": (state.get("errors") or [])[-3:], "submission": state.get("submission"),
            "category": state.get("job", {}).get("category")}


def index_thread(thread_id: str, user_id: str, kind: str, state: dict, status: str) -> None:
    _init()
    job = state.get("job") or {}
    db.execute("delete from threads where thread_id=?", (thread_id,))
    db.execute("insert into threads values(?,?,?,?,?,?,?,?)",
               (thread_id, user_id, kind, job.get("job_id"), job.get("title"), status,
                json.dumps(summarise(state), default=str), time.time()))


async def _drive(graph, inp: Any, cfg: dict, thread_id: str, user_id: str, kind: str = "bid") -> dict:
    trace_id = cfg["configurable"].get("trace_id")
    async for chunk in graph.astream(inp, cfg, stream_mode="updates"):
        for node_name, upd in chunk.items():
            if node_name == "__interrupt__":
                trace.emit("approval", "interrupt", {"waiting_for": "human approval"}, thread_id, trace_id)
            elif isinstance(upd, dict):
                trace.emit(node_name, "update", {"status": upd.get("status"), "keys": sorted(upd)}, thread_id, trace_id)
    snap = await graph.aget_state(cfg)
    state = snap.values
    interrupts = [i.value for t in snap.tasks for i in (t.interrupts or [])]
    status = "awaiting_approval" if interrupts else state.get("status") or "done"
    index_thread(thread_id, user_id, kind, state, status)
    trace.emit("graph", "status", {"status": status}, thread_id, trace_id)
    return {"thread_id": thread_id, "status": status, "state": state, "interrupts": interrupts}


async def start_bid(graph, job: dict, user_id: str, persona: str, thread_id: str | None = None,
                    client: dict | None = None) -> dict:
    thread_id = thread_id or bid_thread_id(user_id, job["job_id"])
    trace_id = "tr_" + uuid.uuid4().hex[:16]
    cfg = {"configurable": {"thread_id": thread_id, "trace_id": trace_id}, "recursion_limit": 60,
           "run_name": f"bid:{job['job_id']}", "metadata": {"trace_id": trace_id, "user_id": user_id}}
    snap = await graph.aget_state(cfg)
    if snap.values:                         # idempotent: an existing thread is never re-analysed
        return await _drive(graph, None, cfg, thread_id, user_id) if snap.next and not snap.tasks[0].interrupts \
            else {"thread_id": thread_id, "status": "exists", "state": snap.values,
                  "interrupts": [i.value for t in snap.tasks for i in (t.interrupts or [])]}
    route = None
    if persona == "agency":                 # X1: pick the best-fit team member first (agent/agency.py)
        from agent.agency import route_job
        if "title" not in job:              # the worker queues bare ids; routing needs the post's skills
            from agent.mcp_client import ToolLayer
            job = {**await ToolLayer(user_id, "agency", trace_id).call("scout", "marketplace.get_job",
                                                                         {"job_id": job["job_id"]}), **job}
        r = await route_job(job, user_id, trace_id)
        route = r.model_dump()
        trace.emit("agency", "route", route, thread_id, trace_id)
        if r.member is None:
            return {"thread_id": thread_id, "status": "skipped", "state": {"agency_route": route}, "interrupts": []}
        persona = r.member
    inp = {"thread_id": thread_id, "user_id": user_id, "persona": persona, "trace_id": trace_id, "job": job,
           "profile": profile_for(persona), "status": "analysing", "revisions": 0}
    if route:
        inp["agency_route"] = route
    if client:
        inp["client"] = client
    trace.emit("graph", "start", {"job_id": job["job_id"]}, thread_id, trace_id)
    return await _drive(graph, inp, cfg, thread_id, user_id)


async def resume(graph, thread_id: str, user_id: str, decision: dict, kind: str = "bid") -> dict:
    snap = await graph.aget_state({"configurable": {"thread_id": thread_id}})
    if not snap.values:
        raise KeyError(f"no thread {thread_id}")
    if snap.values.get("user_id") != user_id:
        raise PermissionError("thread belongs to another user")
    if not any(t.interrupts for t in snap.tasks):
        raise ValueError(f"thread {thread_id} is not waiting for approval (status {snap.values.get('status')})")
    trace_id = snap.values.get("trace_id")
    cfg = {"configurable": {"thread_id": thread_id, "trace_id": trace_id}, "recursion_limit": 60}
    trace.emit("approval", "resume", {"action": decision.get("action")}, thread_id, trace_id)
    return await _drive(graph, Command(resume={**decision, "user_id": user_id}), cfg, thread_id, user_id, kind)


async def continue_thread(graph, thread_id: str, user_id: str, kind: str = "bid") -> dict:
    """Re-run a thread from its last checkpoint (degraded / crashed jobs; also 'replay from node')."""
    snap = await graph.aget_state({"configurable": {"thread_id": thread_id}})
    cfg = {"configurable": {"thread_id": thread_id, "trace_id": snap.values.get("trace_id")}, "recursion_limit": 60}
    if snap.values.get("status") == "degraded":
        await graph.aupdate_state(cfg, {"status": "=analysing"})
    return await _drive(graph, None, cfg, thread_id, user_id, kind)


def new_graph(checkpointer, store):
    from agent.runtime import set_store
    set_store(store)
    return build_bid_graph(checkpointer=checkpointer, store=store)


def new_conversation_graph(checkpointer, store):
    from agent.graphs.conversation_graph import build_conversation_graph
    from agent.runtime import set_store
    set_store(store)
    return build_conversation_graph(checkpointer=checkpointer, store=store)


async def start_conversation(graph, conversation_id: str, user_id: str, persona: str, proposal: dict | None = None,
                             job: dict | None = None, new_message_id: str = "") -> dict:
    """One graph thread per client conversation; each new client message is a new run on that thread."""
    thread_id = f"conv-{user_id}-{conversation_id}"
    trace_id = "tr_" + uuid.uuid4().hex[:16]
    cfg = {"configurable": {"thread_id": thread_id, "trace_id": trace_id}, "recursion_limit": 40}
    snap = await graph.aget_state(cfg)
    if snap.values and any(t.interrupts for t in snap.tasks):
        return {"thread_id": thread_id, "status": "awaiting_approval", "state": snap.values,
                "interrupts": [i.value for t in snap.tasks for i in (t.interrupts or [])]}
    if snap.values and new_message_id and snap.values.get("last_seen_message_id") == new_message_id:
        return {"thread_id": thread_id, "status": "exists", "state": snap.values, "interrupts": []}
    inp = {"thread_id": thread_id, "user_id": user_id, "persona": persona, "trace_id": trace_id,
           "conversation_id": conversation_id, "proposal": proposal or {}, "job": job or {}, "status": "reading"}
    trace.emit("graph", "start", {"conversation_id": conversation_id}, thread_id, trace_id)
    return await _drive(graph, inp, cfg, thread_id, user_id, kind="conversation")
