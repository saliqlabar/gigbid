"""Change request (policy 1.1) replay: how the platform policy update changes GigBid's bids, with no code
path chosen by version. Deterministic, no LLM calls.

    python -m evals.change_request.run [--decisions decisions_open] [--letters outcomes_v0 outcomes_best]

1. Builds a GigSim seed folder whose policy.yaml is policy_v1_1.yaml and checks GigSim serves it unchanged
   (GIGSIM_SEED_DIR, the same way the deployed simulator would receive the instructor's update).
2. Re-prices every job the decision eval chose to bid on under policy 1.0 and 1.1 with the production
   `quote()` (same profile, market stats, client and fit score the agent used).
3. Re-checks every drafted cover letter with the production Compliance Guard under both policies.
Writes evals/results/change_request.json.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import yaml

from agent.nodes.compliance import check
from agent.nodes.pricing import quote
from agent.runner import profile_for
from agent.settings import ROOT
from evals.runners.common import RESULTS, read_detail, write_result

HERE = Path(__file__).parent
SEED = ROOT / "gigsim" / "seed"


def served_policy(seed_dir: Path) -> dict:
    """Ask a fresh in-process GigSim, pointed at `seed_dir`, for its policy."""
    os.environ["GIGSIM_SEED_DIR"] = str(seed_dir)
    import importlib

    from fastapi.testclient import TestClient

    import gigsim.app as app_mod
    importlib.reload(app_mod)
    tok = os.getenv("GIGSIM_API_TOKENS", "dev-token").split(",")[0]
    return TestClient(app_mod.app).get("/policy", headers={"Authorization": f"Bearer {tok}"}).json()


def market_stats(jobs: list[dict], category: str) -> dict:
    """Same statistics GigSim's /market/stats returns (historical jobs only)."""
    rows = [j for j in jobs if j.get("historical") and (not category or j.get("category") == category)]
    fixed = sorted(j["budget_max"] for j in rows if j.get("budget_type") == "fixed" and j.get("budget_max"))
    hourly = sorted(j["hourly_high"] for j in rows if j.get("budget_type") == "hourly" and j.get("hourly_high"))

    def q(xs, p):
        return xs[min(len(xs) - 1, int(p * len(xs)))] if xs else None
    return {"fixed_p25": q(fixed, .25), "fixed_p50": q(fixed, .5), "fixed_p75": q(fixed, .75),
            "hourly_p25": q(hourly, .25), "hourly_p50": q(hourly, .5), "hourly_p75": q(hourly, .75)}


def edit_record() -> dict:
    """What had to change in code for 1.1 (from git), and how long it took (start mark -> commit)."""
    def git(*a):
        return subprocess.run(["git", *a], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    sha = git("log", "-1", "--format=%h", "--", "evals/change_request/policy_v1_1.yaml")
    start = (ROOT / ".logs" / "change_request_start")
    return {"commit": sha, "commit_time": git("show", "-s", "--format=%ci", sha) if sha else None,
            "started_at_local": start.read_text().strip() if start.exists() else None,
            "files": git("show", "--stat", "--format=", sha).splitlines() if sha else []}


def main(decisions: str, letters: list[str]) -> None:
    v10 = yaml.safe_load((SEED / "policy.yaml").read_text())
    with tempfile.TemporaryDirectory() as d:
        seed = Path(d) / "seed"
        shutil.copytree(SEED, seed)
        shutil.copy(HERE / "policy_v1_1.yaml", seed / "policy.yaml")
        v11 = served_policy(seed)
    os.environ.pop("GIGSIM_SEED_DIR", None)

    jobs = json.loads((SEED / "jobs.json").read_text())
    by_id = {j["job_id"]: j for j in jobs}
    clients = {c["client_id"]: {k: v for k, v in c.items() if not k.startswith("_")}
               for c in json.loads((SEED / "clients.json").read_text())}
    stats_cache: dict[str, dict] = {}

    priced = []
    for r in read_detail(RESULTS / f"{decisions}_detail.jsonl"):
        if r.get("pred") != "bid" or not r.get("price"):
            continue
        job = by_id[r["job_id"]]
        st = stats_cache.setdefault(job.get("category") or "", market_stats(jobs, job.get("category") or ""))
        args = (job, profile_for(r["persona"]), st, clients.get(job.get("client_id"), {}), float(r["fit"] or 0))
        bal = r["price"].get("connects_balance")
        a, b = quote(*args, v10, bal), quote(*args, v11, bal)
        priced.append({"job_id": r["job_id"], "persona": r["persona"], "bid_type": a.bid_type,
                       "amount_v10": a.amount, "amount_v11": b.amount, "connects_v10": a.connects_cost,
                       "connects_v11": b.connects_cost, "ev_v10": a.expected_value_usd, "ev_v11": b.expected_value_usd,
                       "worth_v10": a.connects_worth_it, "worth_v11": b.connects_worth_it})

    checked = []
    for src in letters:
        p = RESULTS / f"{src}_detail.jsonl"
        if not p.exists():
            continue
        for r in read_detail(p):
            if r.get("cover_letter"):
                a, b = check(r["cover_letter"], v10), check(r["cover_letter"], v11)
                checked.append({"source": src, "job_id": r["job_id"], "words": len(r["cover_letter"].split()),
                                "blocked_v10": a.blocked_reason, "blocked_v11": b.blocked_reason})

    n = len(priced)
    out = {
        "policy_v10": v10, "policy_v11_served_by_gigsim": v11,
        "served_unchanged": v11 == yaml.safe_load((HERE / "policy_v1_1.yaml").read_text()),
        "code_edit": edit_record(),
        "pricing": {
            "source": f"{decisions}_detail.jsonl", "bids": n,
            "connects_spent_v10": sum(p["connects_v10"] for p in priced),
            "connects_spent_v11": sum(p["connects_v11"] for p in priced),
            "hourly_bids_raised_to_minimum": sum(p["amount_v11"] > p["amount_v10"] for p in priced),
            "hourly_bids": sum(p["bid_type"] == "hourly" for p in priced),
            "mean_ev_v10": round(sum(p["ev_v10"] for p in priced) / n, 2) if n else None,
            "mean_ev_v11": round(sum(p["ev_v11"] for p in priced) / n, 2) if n else None,
            "flipped_to_not_worth_it": sum(p["worth_v10"] and not p["worth_v11"] for p in priced),
            "detail": priced},
        "compliance": {
            "sources": letters, "letters": len(checked),
            "blocked_v10": sum(bool(c["blocked_v10"]) for c in checked),
            "blocked_v11": sum(bool(c["blocked_v11"]) for c in checked),
            "newly_blocked_by_300_word_limit": sum(not c["blocked_v10"] and bool(c["blocked_v11"]) for c in checked),
            "detail": checked},
    }
    path = write_result("change_request", out)
    print(json.dumps({k: {kk: vv for kk, vv in v.items() if kk != "detail"} if isinstance(v, dict) and "detail" in v
                      else v for k, v in out.items() if k not in ("policy_v10", "policy_v11_served_by_gigsim")}, indent=1))
    print("wrote", path)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--decisions", default="decisions_open")
    ap.add_argument("--letters", nargs="*", default=["outcomes_v0", "outcomes_best"])
    a = ap.parse_args()
    main(a.decisions, a.letters)
