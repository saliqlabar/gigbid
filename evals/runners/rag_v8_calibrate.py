"""Calibrate V8's relevance threshold for the reranker actually in use (B, corrective RAG).

V8 keeps a retrieved item for a requirement only if the cross-encoder scores it at least `rag.corrective.relevant`.
Those thresholds were first written for bge-reranker-v2-m3 logits (0.0 / -3.0), but the shipped reranker is
jina-reranker-v1-turbo-en, whose scores are almost all below 0, so V8 kept nearly nothing (rag_variants.json,
v8_corrective uncalibrated). This script measures the reranker's scores on gold vs non-gold evidence and picks the
threshold that maximises F1 of "kept = gold".

Calibration split: the LAST 15 gold-bid jobs per persona (45 jobs). They are also inside the 284-job retrieval
eval set (every variant is scored on the same set), so this tuning touches one number on 16% of the eval jobs;
the report says so.

    python -m evals.runners.rag_v8_calibrate
Writes evals/results/rag_v8_calibration.json.
"""
from __future__ import annotations

import statistics

from evals.runners.common import PERSONAS, dev_set, write_result
from evals.runners.rag_eval import load_requirements
from rag.index.embedder import rerank
from rag.retriever import PRESETS, Pipeline, get_corpus
from rag.variants.base import Query

N_JOBS, REQS_PER_JOB, TOP = 15, 4, 10


def main() -> None:
    pipe = Pipeline(PRESETS["v5_multivector"])   # V8's inner retriever is V5 without decomposition
    gold_s, other_s = [], []
    for per in PERSONAS:
        reqs, corpus = load_requirements(per, "jobspec"), get_corpus(per)
        cal = [r for r in dev_set(per) if r["bid"] and r.get("gold_evidence")][-N_JOBS:]
        for r in cal:
            gold = {g["evidence_id"] for g in r["gold_evidence"]}
            for req in (reqs.get(r["job_id"]) or [])[:REQS_PER_JOB]:
                _, hits = pipe.retrieve_per_query(Query(text=req, persona=per), corpus)[0]
                for h, s in zip(hits, rerank(req, [h.snippet for h in hits[:TOP]])):
                    (gold_s if h.evidence_id in gold else other_s).append(float(s))

    def dec(xs):
        q = statistics.quantiles(xs, n=10)
        return {f"p{10 * (i + 1)}": round(q[i], 3) for i in (0, 2, 4, 6, 8)}

    sweep = []
    for t in [x / 10 for x in range(-50, 11)]:
        tp, fp = sum(s >= t for s in gold_s), sum(s >= t for s in other_s)
        fn = len(gold_s) - tp
        sweep.append({"threshold": t, "kept_gold": round(tp / len(gold_s), 3), "kept_other": round(fp / len(other_s), 3),
                      "f1": round(2 * tp / (2 * tp + fp + fn), 3) if tp else 0.0})
    best = max(sweep, key=lambda r: r["f1"])
    old = next(r for r in sweep if r["threshold"] == 0.0)
    out = {"reranker": "jinaai/jina-reranker-v1-turbo-en", "calibration_jobs": N_JOBS * len(PERSONAS),
           "n_gold_scores": len(gold_s), "n_other_scores": len(other_s),
           "gold_score_deciles": dec(gold_s), "other_score_deciles": dec(other_s),
           "gold_median": round(statistics.median(gold_s), 3), "other_median": round(statistics.median(other_s), 3),
           "old_threshold": old, "best_threshold": best, "sweep": sweep}
    print({k: v for k, v in out.items() if k != "sweep"})
    print("wrote", write_result("rag_v8_calibration", out))


if __name__ == "__main__":
    main()
