"""X1 Agency mode evaluation: does routing each job to the best-fit member pick the right member?

Gold: a dev-set job marked `bid` in persona P's dev set belongs to member P (the dev set is labelled per
persona from that persona's portfolio). Sample: first N such jobs per persona, plus jobs gold-skipped by
everyone would need cross-persona labels we do not have, so we only score routing of jobs someone should take.
Compared with a naive rule (job category -> member), the obvious cheaper alternative.

    python -m evals.runners.agency_eval --per-persona 15
"""
from __future__ import annotations

import argparse
import asyncio
import json
import time
from collections import Counter

from evals.runners import harness
from evals.runners.common import PERSONAS, RESULTS, dev_set, percentile, seed_jobs, write_result

NAIVE = {"AI/ML": "ai_ml", "Data science": "ai_ml", "Web development": "fullstack", "Developer": "fullstack",
         "Data analysis": "data_analyst"}


async def main(a) -> None:
    from agent.agency import route_job
    harness.fresh_gigsim()
    rows = []
    for p in PERSONAS:
        gold_rows = [r for r in dev_set(p) if r["bid"] and not r["scam"]][:a.per_persona]
        for r in gold_rows:
            job = seed_jobs()[r["job_id"]]
            t = time.perf_counter()
            route = await route_job(job, "eval", f"tr_agency_{r['job_id']}")
            rows.append({"job_id": r["job_id"], "gold": p, "category": job.get("category"), "routed": route.member,
                         "naive": NAIVE.get(job.get("category")), "ranking": [m.model_dump() for m in route.ranking],
                         "latency_s": round(time.perf_counter() - t, 2)})
            print(f"{r['job_id']} gold={p} routed={route.member} naive={rows[-1]['naive']}", flush=True)
    (RESULTS / f"{a.out}_detail.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")

    def acc(key, sub):
        return round(sum(r[key] == r["gold"] for r in sub) / len(sub), 4) if sub else None
    out = {"per_persona": a.per_persona, "jobs": len(rows),
           "routing_accuracy": acc("routed", rows), "naive_category_accuracy": acc("naive", rows),
           "by_member": {p: {"routing": acc("routed", [r for r in rows if r["gold"] == p]),
                             "naive": acc("naive", [r for r in rows if r["gold"] == p])} for p in PERSONAS},
           "by_category": {c: {"n": len(v := [r for r in rows if r["category"] == c]), "routing": acc("routed", v),
                               "naive": acc("naive", v)} for c in sorted({r["category"] for r in rows})},
           "confusion": dict(Counter(f"{r['gold']}->{r['routed']}" for r in rows)),
           "latency_p50_s": percentile([r["latency_s"] for r in rows], .5),
           "latency_p95_s": percentile([r["latency_s"] for r in rows], .95),
           "detail_file": f"evals/results/{a.out}_detail.jsonl"}
    print(json.dumps({k: out[k] for k in ("routing_accuracy", "naive_category_accuracy", "confusion")}, indent=1))
    write_result(a.out, out)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-persona", type=int, default=15)
    ap.add_argument("--out", default="agency_eval")
    asyncio.run(main(ap.parse_args()))
