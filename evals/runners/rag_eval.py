"""Retrieval evaluation (B4): evidence Recall@5, MRR, nDCG@10 per variant and persona.

    python -m evals.runners.rag_eval                       # all variants, full dev set (bid jobs with gold evidence)
    python -m evals.runners.rag_eval --variants v0_naive v5_multivector --limit 50
    python -m evals.runners.rag_eval --ablate v5_multivector

Queries: the job post text; requirements for decomposition come from the Job Analyst's cached JobSpecs
(evals/results/jobspecs_<persona>.jsonl, made by evals/runners/build_jobspecs.py) or, with
--requirements tags, from the job post's own public skill tags. Gold labels are never used as queries.
Writes evals/results/rag_variants.json (or rag_ablation.json).
"""
from __future__ import annotations

import argparse
import json
import time
from statistics import mean

from evals.runners.common import (
    PERSONAS,
    RESULTS,
    dev_set,
    job_text,
    mrr,
    ndcg_at,
    percentile,
    recall_at,
    seed_jobs,
    write_result,
)
from rag.retriever import PRESETS, Pipeline, get_corpus
from rag.variants.base import Query


def load_requirements(persona: str, source: str) -> dict[str, list[str]]:
    if source == "tags":
        return {}
    p = RESULTS / f"jobspecs_{persona}.jsonl"
    if not p.exists():
        raise SystemExit(f"{p} missing: run python -m evals.runners.build_jobspecs, or pass --requirements tags")
    out = {}
    for line in p.read_text().splitlines():
        r = json.loads(line)
        out[r["job_id"]] = r["requirements"]
    return out


def evaluate(pipe: Pipeline, persona: str, rows: list[dict], reqs: dict[str, list[str]], source: str) -> dict:
    corpus = get_corpus(persona)
    jobs = seed_jobs()
    r5, m, n10, lat, per_type = [], [], [], [], {}
    for row in rows:
        job = jobs[row["job_id"]]
        requirements = reqs.get(row["job_id"]) if source != "tags" else job.get("skills", [])
        q = Query(text=job_text(job), persona=persona, requirements=requirements or [], category=job.get("category"))
        t = time.perf_counter()
        ranked = [h.evidence_id for h in pipe.retrieve(q, corpus, k=10)]
        lat.append(time.perf_counter() - t)
        grades = {g["evidence_id"]: g["grade"] for g in row["gold_evidence"]}
        gold = set(grades)
        r5.append(recall_at(ranked, gold, 5))
        m.append(mrr(ranked, gold))
        n10.append(ndcg_at(ranked, grades, 10))
        bucket = "4+ requirements" if len(row.get("required_skills", [])) >= 4 else "1-3 requirements"
        per_type.setdefault(bucket, []).append(r5[-1])
    return {"n": len(rows), "recall@5": round(mean(r5), 4), "mrr": round(mean(m), 4), "ndcg@10": round(mean(n10), 4),
            "latency_p50_s": round(percentile(lat, .5), 4), "latency_p95_s": round(percentile(lat, .95), 4),
            "recall@5_by_job_type": {k: {"n": len(v), "recall@5": round(mean(v), 4)} for k, v in per_type.items()}}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--variants", nargs="*", default=list(PRESETS))
    ap.add_argument("--ablate", help="run the named preset with each component removed in turn")
    ap.add_argument("--limit", type=int, default=0, help="jobs per persona (0 = all)")
    ap.add_argument("--requirements", choices=["jobspec", "tags"], default="jobspec")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()

    if a.ablate:
        base = PRESETS[a.ablate]
        runs = {a.ablate: base} | {f"{a.ablate} - {f}": base.without(f) for f in
                                   ("decompose", "hybrid", "rerank", "outcome_boost", "multivector", "corrective")
                                   if getattr(base, f) is True}
    else:
        runs = {v: PRESETS[v] for v in a.variants}

    table = {}
    for name, flags in runs.items():
        pipe = Pipeline(flags, name)
        table[name] = {"flags": flags.as_dict(), "personas": {}}
        for persona in PERSONAS:
            rows = [r for r in dev_set(persona) if r["bid"] and r["gold_evidence"]]
            rows = rows[:a.limit] if a.limit else rows
            table[name]["personas"][persona] = evaluate(pipe, persona, rows, load_requirements(persona, a.requirements),
                                                        a.requirements)
        ps = table[name]["personas"].values()
        table[name]["overall"] = {k: round(mean(p[k] for p in ps), 4) for k in ("recall@5", "mrr", "ndcg@10")}
        print(f"{name:32s} R@5={table[name]['overall']['recall@5']:.3f} MRR={table[name]['overall']['mrr']:.3f} "
              f"nDCG@10={table[name]['overall']['ndcg@10']:.3f}", flush=True)

    out = a.out or ("rag_ablation" if a.ablate else "rag_variants")
    path = write_result(out, {"requirements_source": a.requirements, "limit_per_persona": a.limit or "all",
                              "metric_note": "macro-average over personas; gold = dev set gold_evidence",
                              "results": table})
    print("wrote", path)


if __name__ == "__main__":
    main()
