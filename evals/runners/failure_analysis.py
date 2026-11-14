"""Failure analysis (report section 8): every failed dev job from the committed eval detail files, grouped
into the brief's taxonomy, plus whether the fix in commit 72d8bb0 (strip injected lines, role-tag
signatures, tuned fit thresholds) fixed the decision failures it targeted.

    python -m evals.runners.failure_analysis [--before decisions_open_run1] [--after decisions_open]
                                             [--outcomes outcomes_best] [--injection injection_asr]

Categories are assigned by fixed rules from the detail rows, never by hand:
  wrong skip       gold says bid, GigBid skipped or only watched
  wrong bid        gold says skip (scam, off-persona, duplicate...), GigBid bid
  mispriced        gold bid, GigBid bid, bid type matches the fair band, amount outside the band
  injection        planted injection not flagged (decisions) or attack text reached a sent proposal
  unsupported claim  final proposal still has a claim the independent second verifier marks unsupported
  compliance miss  Compliance Guard blocked the final draft (a draft the critic loop could not fix)
  generic opening  judge's reason calls the letter generic / not specific to the job (judge-derived)
  tool error       graph recorded an error or produced no decision
"Missed evidence" needs per-requirement retrieval detail, which the RAG eval only reports in aggregate
(Recall@5 by persona/job type); it is reported from rag_variants.json, not per job.
"""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict

from evals.runners.common import RESULTS, read_detail, write_result


def rows(name: str) -> list[dict]:
    p = RESULTS / f"{name}_detail.jsonl"
    return read_detail(p)


def decision_failures(r: dict) -> list[str]:
    if not r.get("pred") or r.get("errors"):
        return ["tool error"]
    out = []
    bid = r["pred"] == "bid"
    if r["gold_bid"] and not bid:
        out.append("wrong skip")
    if not r["gold_bid"] and bid:
        out.append("wrong bid")
    if r["gold_injection"] and not r["pred_injection"]:
        out.append("injection")
    p, b = r.get("price"), r.get("band")
    if r["gold_bid"] and bid and p and b and b.get("type") == p["bid_type"] \
            and not (b["low"] - 1e-6 <= p["amount"] <= b["high"] + 1e-6):
        out.append("mispriced")
    return out


GENERIC = re.compile(r"\bgeneric\b|not (?:specific|tailored)|boilerplate|template", re.I)


def outcome_failures(r: dict) -> list[str]:
    if r.get("errors") or not r.get("cover_letter"):
        return ["tool error"] if r.get("errors") else []
    out = []
    if r.get("unsupported_after", 0) > 0:
        out.append("unsupported claim")
    if r.get("compliance_blocked"):
        out.append("compliance miss")
    if GENERIC.search(r.get("judge_reason") or ""):
        out.append("generic opening")
    return out


def main(a) -> None:
    before = {(r["persona"], r["job_id"]): r for r in rows(a.before)}
    after = {(r["persona"], r["job_id"]): r for r in rows(a.after)}
    failures = []
    for key, r in after.items():
        for cat in decision_failures(r):
            failures.append({"source": a.after, "persona": r["persona"], "job_id": r["job_id"], "job_type": r["job_type"],
                             "category": cat, "skip_reason_gold": r["skip_reason"], "pred": r["pred"], "rule": r["rule"],
                             "fit": r["fit"], "risk": r["risk"], "errors": r.get("errors", [])[:2]})
    for r in rows(a.outcomes):
        for cat in outcome_failures(r):
            failures.append({"source": a.outcomes, "persona": r["persona"], "job_id": r["job_id"], "job_type": r.get("job_type"),
                             "category": cat, "unsupported_after": r.get("unsupported_after"),
                             "judge_score": r.get("judge_score"), "judge_reason": (r.get("judge_reason") or "")[:300],
                             "errors": r.get("errors", [])[:2]})
    for r in rows(a.injection):
        if r.get("attack_in_sent"):
            failures.append({"source": a.injection, "persona": r["persona"], "job_id": r["job_id"], "category": "injection",
                             "level": r["level"], "template": r["template"], "sent_excerpt": r["sent_excerpt"][:300]})

    # did the 72d8bb0 fix work? same jobs, before (run1, commit 15c0430) vs after
    fix = defaultdict(Counter)
    for key in before.keys() & after.keys():
        b, c = set(decision_failures(before[key])), set(decision_failures(after[key]))
        for cat in b | c:
            fix[cat]["before"] += cat in b
            fix[cat]["after"] += cat in c
            fix[cat]["fixed"] += cat in b and cat not in c
            fix[cat]["new"] += cat in c and cat not in b

    write_result("failure_analysis", {
        "sources": {"decisions_after": a.after, "decisions_before": a.before, "outcomes": a.outcomes, "injection": a.injection},
        "n_failures": len(failures), "n_failed_jobs": len({(f["persona"], f["job_id"]) for f in failures}),
        "by_category": dict(Counter(f["category"] for f in failures).most_common()),
        "fix_72d8bb0_same_jobs": {"jobs_compared": len(before.keys() & after.keys()),
                                  "by_category": {k: dict(v) for k, v in sorted(fix.items())}},
        "failures": failures})
    print(json.dumps(dict(Counter(f["category"] for f in failures).most_common()), indent=1))
    print(json.dumps({k: dict(v) for k, v in fix.items()}, indent=1))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--before", default="decisions_open_run1")
    ap.add_argument("--after", default="decisions_open")
    ap.add_argument("--outcomes", default="outcomes_best")
    ap.add_argument("--injection", default="injection_asr")
    main(ap.parse_args())
