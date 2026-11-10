"""Bid graph against GigSim (in-process) with the scripted test model."""
import pytest

from agent.persistence import open_persistence
from agent.runner import new_graph, resume, start_bid

BID_JOB = "job_00281"        # in-persona React job (fullstack)


async def balance(gigsim_adapter):
    return (await gigsim_adapter.get_connects_balance("fullstack"))["balance"]


async def test_bid_pauses_for_approval_then_submits_once(env, gigsim_adapter):
    async with open_persistence() as (cp, store):
        g = new_graph(cp, store)
        r = await start_bid(g, {"job_id": BID_JOB}, "reviewer", "fullstack")
        assert r["status"] == "awaiting_approval", r["state"].get("errors")
        s = r["state"]
        # parallel branches all reported, then decide -> pricing -> writer -> verifier -> critic -> compliance
        assert {"job_spec", "risk_report", "fit_report", "decision", "price_quote", "verification"} <= set(s)
        p = r["interrupts"][0]
        assert p["dry_run_preview"]["status"] == "preview" and "[" not in p["dry_run_preview"]["would_send"]["cover_letter"]
        before = await balance(gigsim_adapter)
        r2 = await resume(g, r["thread_id"], "reviewer", {"action": "approve"})
        assert r2["status"] == "submitted"
        assert await balance(gigsim_adapter) == before - p["dry_run_preview"]["connects_cost"]
        with pytest.raises(ValueError):                 # nothing left to approve: cannot submit twice
            await resume(g, r["thread_id"], "reviewer", {"action": "approve"})


async def test_crash_and_resume_submits_exactly_once(env, gigsim_adapter):
    """Kill the process while the proposal waits for approval; a new process approves; one submission."""
    async with open_persistence() as (cp, store):
        r = await start_bid(new_graph(cp, store), {"job_id": BID_JOB}, "reviewer", "fullstack")
        tid = r["thread_id"]
        cost = r["interrupts"][0]["dry_run_preview"]["connects_cost"]
    before = await balance(gigsim_adapter)
    # "restart": brand-new checkpointer connection and graph object, same durable files
    async with open_persistence() as (cp, store):
        g = new_graph(cp, store)
        r2 = await resume(g, tid, "reviewer", {"action": "approve"})
        assert r2["status"] == "submitted"
        # a crashed worker re-running the submitter (same idempotency keys) must not spend again
        from agent.nodes.approval import submitter
        again = await submitter(r2["state"])
        assert again["submission"]["replayed"] is True
    assert await balance(gigsim_adapter) == before - cost
    props = await gigsim_adapter.list_proposals("fullstack")
    assert sum(p["status"] == "submitted" for p in props) == 1


async def test_edit_goes_back_through_compliance(env, gigsim_adapter):
    async with open_persistence() as (cp, store):
        g = new_graph(cp, store)
        r = await start_bid(g, {"job_id": BID_JOB}, "reviewer", "fullstack")
        bad = "Contact me on WhatsApp +92 300 1234567, I guarantee results. " + r["interrupts"][0]["draft"]["cover_letter"]
        r2 = await resume(g, r["thread_id"], "reviewer", {"action": "edit", "edited_text": bad})
        assert r2["status"] == "awaiting_approval"
        p = r2["interrupts"][0]
        assert not p["compliance"]["allowed"] and "approve" not in p["allowed_actions"]
        assert "+92" not in p["draft"]["cover_letter"]
        assert r2["state"]["approvals"][-1]["diff"]
        r3 = await resume(g, r["thread_id"], "reviewer", {"action": "reject", "reason": "test"})
        assert r3["status"] == "rejected"


async def test_reject_records_reason(env, gigsim_adapter):
    from agent.memory.store import observations
    async with open_persistence() as (cp, store):
        g = new_graph(cp, store)
        r = await start_bid(g, {"job_id": BID_JOB}, "reviewer", "fullstack")
        r2 = await resume(g, r["thread_id"], "reviewer", {"action": "reject", "reason": "budget too low"})
        assert r2["status"] == "rejected" and r2["state"].get("submission") is None
    assert observations("reviewer", "rejection")[0]["reason"] == "budget too low"


async def test_style_memory_promotes_only_after_repeated_edits(env, gigsim_adapter):
    from agent.memory.store import list_memory, record_edit
    async with open_persistence() as (_, store):
        for i in range(2):
            await record_edit(store, "reviewer", "I am passionate about this. You need a React app.",
                              "You need a React app.", f"t{i}")
        assert await list_memory(store, "reviewer") == []          # 2 of 5: not promoted
        await record_edit(store, "reviewer", "I am passionate about React. You need X.", "You need X.", "t3")
        keys = {m["key"] for m in await list_memory(store, "reviewer")}
        assert "remove_word:passionate" in keys


async def test_scam_job_is_skipped(env, gigsim_adapter):
    import json

    from agent.marketplace_port import MarketplaceError
    from agent.settings import ROOT
    scam_ids = [json.loads(x)["job_id"] for x in (ROOT / "evals/datasets/dev_fullstack.jsonl").read_text().splitlines()
                if json.loads(x)["scam"]]
    scam = None
    for jid in scam_ids:
        try:
            scam = await gigsim_adapter.get_job(jid)
            break
        except MarketplaceError:
            continue
    assert scam, "no published scam job in the seed"
    async with open_persistence() as (cp, store):
        r = await start_bid(new_graph(cp, store), {"job_id": scam["job_id"]}, "reviewer", "fullstack")
    assert r["status"] == "skipped" and r["state"]["decision"]["rule"] in ("scam", "maximum_risk_score")


async def test_marketplace_down_marks_degraded(env, gigsim_adapter):
    from agent.marketplace_port import MarketplaceUnavailable
    from mcp_servers.marketplace import server as msrv

    class Down:
        name = "gigsim"

        async def get_job(self, *a, **k):
            raise MarketplaceUnavailable("connection refused")
        get_policy = get_job
    msrv.set_port(Down())
    async with open_persistence() as (cp, store):
        r = await start_bid(new_graph(cp, store), {"job_id": BID_JOB}, "reviewer", "fullstack")
    assert r["status"] == "degraded"
