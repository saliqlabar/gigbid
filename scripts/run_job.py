"""Run one GigSim job through the bid graph from the terminal (single-agent demo, A-part deliverable).

    python scripts/run_job.py --job job_00281 --persona fullstack [--approve | --reject "reason"]
Needs GigSim running (uvicorn gigsim.app:app --port 8081) and the model profile reachable.
"""
import argparse
import asyncio
import json

from agent.persistence import open_persistence
from agent.runner import new_graph, resume, start_bid


async def main(a):
    async with open_persistence() as (cp, store):
        g = new_graph(cp, store)
        res = await start_bid(g, {"job_id": a.job}, a.user, a.persona, thread_id=a.thread)
        s = res["state"]
        print("STATUS:", res["status"], "| steps", s.get("steps"), "| tokens", s.get("tokens"))
        for k in ("decision", "risk_report", "fit_report", "price_quote"):
            print(f"\n== {k}\n", json.dumps(s.get(k), indent=1)[:1500])
        if res["interrupts"]:
            p = res["interrupts"][0]
            print("\n== DRAFT\n", p["draft"]["cover_letter"])
            print("\n== CLAIMS"); [print(" -", c["status"], "|", c["claim"][:110], c["evidence_ids"]) for c in p["claims"]]
            print("\n== CRITIQUES", json.dumps(s.get("critiques"), indent=1)[:1500])
            print("\n== COMPLIANCE", json.dumps(p["compliance"], indent=1)[:800])
            print("\n== DRY RUN", json.dumps(p["dry_run_preview"], indent=1)[:600])
            if a.approve or a.reject:
                dec = {"action": "approve"} if a.approve else {"action": "reject", "reason": a.reject}
                r2 = await resume(g, res["thread_id"], a.user, dec)
                print("\nAFTER RESUME:", r2["status"], json.dumps(r2["state"].get("submission"), indent=1))
        if s.get("errors"):
            print("\nERRORS:", s["errors"])


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--job", required=True)
    ap.add_argument("--persona", default="fullstack")
    ap.add_argument("--user", default="reviewer")
    ap.add_argument("--thread")
    ap.add_argument("--approve", action="store_true")
    ap.add_argument("--reject")
    asyncio.run(main(ap.parse_args()))
