"""CI smoke eval: 30 dev jobs through the decision path + 5 full proposals, gated by evals/thresholds.yaml.
    MODEL_PROFILE=test python -m evals.runners.smoke_eval --jobs 30
Exits 1 if bid-or-skip F1 drops or unsupported claims rise past the thresholds."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys

import yaml

from evals.runners import harness
from evals.runners.common import ROOT, write_result
from evals.runners.decision_eval import metrics, run_one, sample


async def proposals(n: int) -> list[int]:
    from agent.runner import profile_for
    from rag.verify import verify_text
    g, _ = harness.full_graph()
    rows = [r for r in sample("fullstack", 200) if r["bid"]][:n]
    out = []
    for r in rows:
        tid = f"smoke-{r['job_id']}"
        cfg = {"configurable": {"thread_id": tid}, "recursion_limit": 60}
        await g.ainvoke({"thread_id": tid, "user_id": "eval", "persona": "fullstack", "trace_id": "tr_smoke",
                         "job": {"job_id": r["job_id"]}, "profile": profile_for("fullstack"), "status": "analysing",
                         "revisions": 0}, cfg)
        s = (await g.aget_state(cfg)).values
        if s.get("proposal_draft"):
            ev = {e["evidence_id"]: e["snippet"] for e in s.get("evidence", [])}
            v = await verify_text(s["proposal_draft"]["cover_letter"], ev)     # independent post-hoc check
            out.append(sum(c.status == "unsupported" for c in v.claims))
    return out


async def main(a):
    th = yaml.safe_load((ROOT / "evals" / "thresholds.yaml").read_text())["smoke"]
    harness.fresh_gigsim()
    g, _ = harness.analysis_graph()
    sem = asyncio.Semaphore(4)
    per = a.jobs // 3
    rows = []
    for p in ("ai_ml", "fullstack", "data_analyst"):
        rows += await asyncio.gather(*[run_one(g, r, p, sem) for r in sample(p, per)])
    m = metrics(rows)
    unsup = await proposals(5)
    m["unsupported_claims_per_proposal"] = round(sum(unsup) / len(unsup), 3) if unsup else None
    m["proposals_checked"] = len(unsup)
    fails = []
    if m["bid_f1"] < th["min_bid_f1"]:
        fails.append(f"bid_f1 {m['bid_f1']} < {th['min_bid_f1']}")
    if m["scam_recall"] < th["min_scam_recall"]:
        fails.append(f"scam_recall {m['scam_recall']} < {th['min_scam_recall']}")
    if m["unsupported_claims_per_proposal"] is None or m["unsupported_claims_per_proposal"] > th["max_unsupported_claims_per_proposal"]:
        fails.append(f"unsupported claims {m['unsupported_claims_per_proposal']} > {th['max_unsupported_claims_per_proposal']}")
    if (m["price_in_band"] or 0) < th["min_price_in_band"]:
        fails.append(f"price_in_band {m['price_in_band']} < {th['min_price_in_band']}")
    write_result("smoke_eval", {"thresholds": th, "metrics": m, "failures": fails})
    print(json.dumps(m, indent=1))
    print("SMOKE EVAL", "FAILED: " + "; ".join(fails) if fails else "passed")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--jobs", type=int, default=30)
    asyncio.run(main(ap.parse_args()))
