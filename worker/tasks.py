"""Background work (E1). Everything long-running happens here, never inside a web request:

- schedule (cron, every minute): for each user and saved search whose interval has elapsed, queue scout_user.
- scout_user: run saved searches through the Scout (marketplace MCP), dedupe, queue analyse_job per new job.
- analyse_job: run the bid graph for one job until it finishes or pauses for approval.
- poll_events (cron): marketplace events -> outcomes into memory (promotion rules) and client messages ->
  handle_conversation (conversation graph).
- requeue_degraded (cron): threads that hit "marketplace unavailable" are continued once it is back.

Idempotency: arq job ids are the graph thread ids (a job is queued at most once at a time), `start_bid`
refuses to re-analyse an existing thread, and every marketplace write carries a thread-derived
idempotency key, so a worker restart never double-analyses or double-submits.
"""
from __future__ import annotations

import json
import time

from agent import db
from agent.mcp_client import MarketplaceDown, ToolLayer
from agent.nodes.scout import scout_saved_search, shingles
from agent.runner import bid_thread_id, index_thread, start_bid, start_conversation
from agent.runner import continue_thread as runner_continue
from apps.api.auth.users import all_users, get_user

_SCHEMA = """
create table if not exists scout_state(user_id text, search text, last_run real, last_seen text, primary key(user_id, search));
create table if not exists seen_jobs(user_id text, job_id text, sig text, primary key(user_id, job_id));
create table if not exists event_cursor(user_id text primary key, seq integer)
"""
_ready = False


def _init():
    global _ready
    if not _ready:
        db.executescript(_SCHEMA)
        _ready = True


def _graphs(ctx):
    if "app_state" in ctx:                       # inline (tests): reuse the API's graphs
        s = ctx["app_state"]
        return s.bid_graph, s.conv_graph, s.store
    return ctx["bid_graph"], ctx["conv_graph"], ctx["store"]


async def _enqueue(ctx, fn, *args, job_id=None):
    if "redis" in ctx:
        return await ctx["redis"].enqueue_job(fn, *args, _job_id=job_id)
    return await globals()[fn](ctx, *args)


def tools(user: dict, purpose: str = "worker") -> ToolLayer:
    return ToolLayer(user["user_id"], user["persona"], f"tr_worker_{purpose}_{int(time.time())}")


async def schedule(ctx):
    _init()
    now = time.time()
    for u in all_users():
        for s in u["settings"].get("saved_searches", []):
            interval = max(int(s.get("interval_min", 30)), 60 if u["settings"].get("marketplace") == "upwork_live" else 15)
            row = db.query("select last_run from scout_state where user_id=? and search=?", (u["user_id"], s["name"]))
            if not row or now - (row[0]["last_run"] or 0) >= interval * 60:
                await _enqueue(ctx, "scout_user", u["user_id"], s["name"], job_id=f"scout-{u['user_id']}-{s['name']}")


async def scout_user(ctx, user_id: str, search_name: str | None = None):
    _init()
    u = get_user(user_id)
    seen = [{"job_id": r["job_id"], "sig": set(json.loads(r["sig"]))}
            for r in db.query("select job_id, sig from seen_jobs where user_id=?", (user_id,))]
    queued = []
    for s in u["settings"].get("saved_searches", []):
        if search_name and s["name"] != search_name:
            continue
        st = db.query("select last_seen from scout_state where user_id=? and search=?", (user_id, s["name"]))
        since = st[0]["last_seen"] if st else ""
        try:
            new = await scout_saved_search(tools(u, "scout"), s, seen, since=since or "")
        except MarketplaceDown:
            return {"degraded": True}
        for j in new:
            db.execute("insert or ignore into seen_jobs values(?,?,?)" if not db.is_pg() else
                       "insert into seen_jobs values(?,?,?) on conflict do nothing",
                       (user_id, j["job_id"], json.dumps(sorted(shingles(j["title"] + " " + j.get("description", ""))))))
            await _enqueue(ctx, "analyse_job", user_id, j["job_id"], j.get("duplicate_of"),
                           job_id=bid_thread_id(user_id, j["job_id"]))
            queued.append(j["job_id"])
        latest = max([j.get("published_at") or "" for j in new] + [since or ""])
        db.execute("delete from scout_state where user_id=? and search=?", (user_id, s["name"]))
        db.execute("insert into scout_state values(?,?,?,?)", (user_id, s["name"], time.time(), latest))
    return {"queued": queued}


async def connects_remaining(u: dict) -> int | None:
    """Weekly Connects budget minus what was spent in the last 7 days (from submitted threads)."""
    budget = u["settings"].get("weekly_connects_budget")
    if budget is None:
        return None
    week = time.time() - 7 * 24 * 3600
    rows = db.query("select summary from threads where user_id=? and status='submitted' and updated>?", (u["user_id"], week))
    spent = sum((json.loads(r["summary"]).get("submission") or {}).get("connects_spent") or 0 for r in rows)
    return int(budget) - spent


async def analyse_job(ctx, user_id: str, job_id: str, duplicate_of: str | None = None):
    bid, _, _ = _graphs(ctx)
    u = get_user(user_id)
    job = {"job_id": job_id, "duplicate_of": duplicate_of}
    tid = bid_thread_id(user_id, job_id)
    res = await start_bid(bid, job, user_id, u["persona"], thread_id=tid)
    st = res["state"]
    if u["settings"].get("autonomy") == "auto_skip_low_fit" and res["status"] == "skipped" and \
            (st.get("decision") or {}).get("rule") in ("watch_fit_score", "duplicate"):
        index_thread(tid, user_id, "bid", st, "auto_skipped")     # never auto-SEND: only auto-skip
    return {"thread_id": tid, "status": res["status"]}


async def continue_thread(ctx, user_id: str, thread_id: str):
    bid, conv, _ = _graphs(ctx)
    g = conv if thread_id.startswith("conv-") else bid
    res = await runner_continue(g, thread_id, user_id, "conversation" if thread_id.startswith("conv-") else "bid")
    return {"thread_id": thread_id, "status": res["status"]}



async def handle_conversation(ctx, user_id: str, conversation_id: str, message_id: str = ""):
    bid, conv, _ = _graphs(ctx)
    u = get_user(user_id)
    rows = db.query("select thread_id from threads where user_id=? and kind='bid'", (user_id,))
    proposal = {}
    for r in rows:                                     # find our proposal for this conversation's job
        snap = await bid.aget_state({"configurable": {"thread_id": r["thread_id"]}})
        sub = (snap.values or {}).get("submission") or {}
        if sub.get("proposal_id") and conversation_id.endswith(sub["proposal_id"][5:]):
            proposal = {**snap.values.get("proposal_draft", {}), "proposal_id": sub["proposal_id"]}
            break
    res = await start_conversation(conv, conversation_id, user_id, u["persona"], proposal=proposal,
                                   new_message_id=message_id)
    return {"thread_id": res["thread_id"], "status": res["status"]}


async def poll_events(ctx):
    _init()
    _, _, store = _graphs(ctx)
    from agent.memory.store import record_outcome
    for u in all_users():
        cur = db.query("select seq from event_cursor where user_id=?", (u["user_id"],))
        after = cur[0]["seq"] if cur else 0
        try:
            res = await tools(u, "events").call("worker", "marketplace.list_events", {"after": after})
        except MarketplaceDown:
            continue
        for e in res["events"]:
            d = e["data"]
            if e["kind"] == "client_message":
                await _enqueue(ctx, "handle_conversation", u["user_id"], d["thread_id"], str(e["seq"]),
                               job_id=f"conv-{u['user_id']}-{d['thread_id']}-{e['seq']}")
            elif e["kind"] in ("proposal_lost", "proposal_replied", "proposal_interview", "proposal_hired"):
                tid = bid_thread_id(u["user_id"], d.get("job_id", ""))
                bid, _, _ = _graphs(ctx)
                snap = await bid.aget_state({"configurable": {"thread_id": tid}})
                s = snap.values or {}
                if s.get("proposal_draft"):
                    await record_outcome(store, u["user_id"], (s.get("job") or {}).get("category") or "",
                                         e["kind"].removeprefix("proposal_"), s["proposal_draft"]["cover_letter"],
                                         s["proposal_draft"].get("evidence_ids", []), d.get("proposal_id", ""))
        if res["events"]:
            db.execute("delete from event_cursor where user_id=?", (u["user_id"],))
            db.execute("insert into event_cursor values(?,?)", (u["user_id"], res["last_seq"]))


async def requeue_degraded(ctx):
    rows = db.query("select thread_id, user_id from threads where status='degraded'")
    if not rows:
        return
    u = get_user(rows[0]["user_id"])
    try:
        st = await tools(u, "status").call("worker", "marketplace.marketplace_status", {})
    except MarketplaceDown:
        return
    if st.get("available"):
        for r in rows:
            await _enqueue(ctx, "continue_thread", r["user_id"], r["thread_id"], job_id=f"retry-{r['thread_id']}")
