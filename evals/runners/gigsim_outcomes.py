"""Proposal quality + outcome evaluation on a FIXED job set (B4/B5).

For each job: full bid graph -> (harness approves as the human would) -> submit -> GigSim simulated client
decides (rule mode, deterministic per idempotency key). Per proposal: unsupported claims BEFORE verification
(claims the verifier removed) and AFTER (an independent second verification pass on the final text), critic
revisions, compliance violations caught, judge score, latency, tokens, USD; per set: reply and hire rate.

    python -m evals.runners.gigsim_outcomes --per-persona 5 --variant v2_hybrid --out outcomes_v2
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import time

from evals.runners import harness
from evals.runners.common import PERSONAS, RESULTS, dev_set, job_text, percentile, seed_jobs, write_result


def fixed_set(persona: str, n: int) -> list[dict]:
    """First n gold-bid jobs per persona in dev-set order: the same jobs for every variant and run."""
    return [r for r in dev_set(persona) if r["bid"]][:n]


async def run_one(g, row, persona, sem, adapter) -> dict:
    from langgraph.types import Command

    from agent.runner import profile_for
    from evals.runners.judge import judge
    from rag.verify import verify_text
    async with sem:
        tid = f"out-{persona}-{row['job_id']}"
        cfg = {"configurable": {"thread_id": tid}, "recursion_limit": 80}
        t = time.perf_counter()
        await g.ainvoke({"thread_id": tid, "user_id": "eval", "persona": persona, "trace_id": f"tr_out_{row['job_id']}",
                         "job": {"job_id": row["job_id"]}, "profile": profile_for(persona), "status": "analysing",
                         "revisions": 0}, cfg)
        snap = await g.aget_state(cfg)
        s = snap.values
        paused = any(tk.interrupts for tk in snap.tasks)
        rec = {"job_id": row["job_id"], "persona": persona, "job_type": row["job_type"], "status": s.get("status"),
               "decision": (s.get("decision") or {}).get("action"), "errors": s.get("errors", [])}
        if not paused:
            rec.update(latency_s=round(time.perf_counter() - t, 2), tokens=s.get("tokens", 0), cost_usd=s.get("cost_usd", 0))
            return rec
        draft = s["proposal_draft"]
        v = s.get("verification") or {}
        comp = s.get("compliance_verdict") or {}
        ev = {e["evidence_id"]: e["snippet"] for e in s.get("evidence", [])}
        post = await verify_text(draft["cover_letter"], ev)                       # independent "after" check
        allowed = comp.get("allowed", True)
        if allowed:
            await g.ainvoke(Command(resume={"action": "approve", "user_id": "eval"}), cfg)
        s2 = (await g.aget_state(cfg)).values
        verdict = await judge(job_text(seed_jobs()[row["job_id"]]), draft["cover_letter"])
        rec.update(latency_s=round(time.perf_counter() - t, 2), tokens=s2.get("tokens", 0), cost_usd=s2.get("cost_usd", 0),
                   submitted=s2.get("status") == "submitted", proposal_id=(s2.get("submission") or {}).get("proposal_id"),
                   cover_letter=draft["cover_letter"], words=len(draft["cover_letter"].split()),
                   claims=len([c for c in v.get("claims", []) if c["status"] != "not_a_claim"]),
                   unsupported_before=v.get("unsupported_before", 0),
                   unsupported_after=sum(c.status == "unsupported" for c in post.claims),
                   revisions=s2.get("revisions", 0), critic_approved=(s2.get("critiques") or [{}])[-1].get("approve"),
                   compliance_violations=len(comp.get("violations", [])), compliance_blocked=not allowed,
                   judge_score=verdict.score, judge_reason=verdict.reason, evidence_ids=draft.get("evidence_ids", []))
        return rec


def summarise(rows: list[dict], outcomes: dict[str, str]) -> dict:
    props = [r for r in rows if r.get("cover_letter")]
    sub = [r for r in props if r.get("submitted")]
    for r in sub:
        r["outcome"] = outcomes.get(r["proposal_id"])
    reply = [r for r in sub if r["outcome"] in ("replied", "interview", "hired")]
    lat = [r["latency_s"] for r in props]
    n = len(props) or 1
    return {"jobs": len(rows), "proposals": len(props), "submitted": len(sub),
            "reply_rate": round(len(reply) / len(sub), 4) if sub else None,
            "hire_rate": round(sum(r["outcome"] == "hired" for r in sub) / len(sub), 4) if sub else None,
            "unsupported_before_per_proposal": round(sum(r["unsupported_before"] for r in props) / n, 3),
            "unsupported_after_per_proposal": round(sum(r["unsupported_after"] for r in props) / n, 3),
            "unsupported_rate_before": round(sum(r["unsupported_before"] for r in props) / max(1, sum(r["claims"] + r["unsupported_before"] for r in props)), 4),
            "unsupported_rate_after": round(sum(r["unsupported_after"] for r in props) / max(1, sum(r["claims"] for r in props)), 4),
            "judge_mean": round(sum(r["judge_score"] for r in props) / n, 3) if props else None,
            "critic_revisions_mean": round(sum(r["revisions"] for r in props) / n, 3),
            "compliance_violations_caught": sum(r["compliance_violations"] for r in props),
            "compliance_blocked": sum(r["compliance_blocked"] for r in props),
            "latency_p50_s": percentile(lat, .5), "latency_p95_s": percentile(lat, .95),
            "tokens_per_proposal": round(sum(r["tokens"] for r in props) / n) if props else None,
            "usd_per_proposal": round(sum(r["cost_usd"] for r in props) / n, 6) if props else None,
            "not_drafted": [{"job_id": r["job_id"], "status": r["status"], "decision": r["decision"]} for r in rows if not r.get("cover_letter")]}


async def main(a) -> None:
    if a.variant:
        os.environ["RAG_VARIANT"] = a.variant
    adapter = harness.fresh_gigsim()
    g, _ = harness.full_graph()
    sem = asyncio.Semaphore(a.concurrency)
    detail = RESULTS / f"{a.out}_detail.jsonl"
    rows = []
    for persona in PERSONAS:
        batch = await asyncio.gather(*[run_one(g, r, persona, sem, adapter) for r in fixed_set(persona, a.per_persona)],
                                     return_exceptions=True)
        for b in batch:
            if isinstance(b, dict):
                rows.append(b)
            else:
                print("error:", repr(b)[:300], flush=True)
        print(f"{persona}: {len(rows)} done", flush=True)
    from gigsim import app as gapp
    gapp.tick()                                         # simulated clients decide on every submitted proposal
    outcomes = {}
    for p in PERSONAS:
        for pr in await adapter.list_proposals(p):
            outcomes[pr["proposal_id"]] = pr["status"]
    detail.write_text("\n".join(json.dumps(r, default=str) for r in rows) + "\n")
    from agent.llm import model_for, profile_name
    from rag.retriever import get_retriever
    out = {"profile": profile_name(), "rag_variant": get_retriever().name, "per_persona": a.per_persona,
           "writer_model": model_for("writer")["model"], "client_mode": os.getenv("GIGSIM_CLIENT_MODE", "rule"),
           "overall": summarise(rows, outcomes),
           "by_persona": {p: summarise([r for r in rows if r["persona"] == p], outcomes) for p in PERSONAS},
           "detail_file": str(detail.relative_to(RESULTS.parent.parent))}
    print(json.dumps(out["overall"], indent=1))
    write_result(a.out, out)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-persona", type=int, default=5)
    ap.add_argument("--variant", default=None)
    ap.add_argument("--concurrency", type=int, default=2)
    ap.add_argument("--out", default="outcomes")
    asyncio.run(main(ap.parse_args()))
