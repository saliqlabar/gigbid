"""Judge calibration (B4): the author labels 30 proposals blind with the same 1-5 rubric
(agent/prompts/judge.md), then Cohen's kappa between the author and the LLM judge is reported.

    python -m evals.runners.judge_calibration prepare     # writes evals/datasets/judge_labels.csv (30 rows, blind)
    # ... fill the `my_score` column (1-5) by hand ...
    python -m evals.runners.judge_calibration score       # judge scores the same 30, writes judge_calibration.json

Items come from the committed proposal-outcome detail files, mixed across RAG variants and personas, so the
set covers good and bad letters. The CSV never contains the judge's score, so labelling is blind.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import random
from collections import Counter

from evals.runners.common import DEV, RESULTS, job_text, seed_jobs, write_result

LABELS = DEV / "judge_labels.csv"


def prepare(n: int, sources: list[str]) -> None:
    pool = []
    for src in sources:
        for line in (RESULTS / f"{src}_detail.jsonl").read_text().splitlines():
            r = json.loads(line)
            if r.get("cover_letter"):
                pool.append({"item_id": f"{src}:{r['job_id']}", "job_id": r["job_id"], "letter": r["cover_letter"]})
    seen, uniq = set(), []
    for r in pool:                                   # one letter per (source, job)
        if r["item_id"] not in seen:
            seen.add(r["item_id"])
            uniq.append(r)
    rng = random.Random(4016)
    rng.shuffle(uniq)
    rows = uniq[:n]
    with LABELS.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["item_id", "job_id", "job_title", "job_post", "proposal", "my_score", "my_note"])
        for r in rows:
            j = seed_jobs()[r["job_id"]]
            w.writerow([r["item_id"], r["job_id"], j["title"], j["description"][:1500], r["letter"], "", ""])
    print(f"wrote {len(rows)} items to {LABELS}; fill my_score (1-5) using agent/prompts/judge.md's rubric")


def kappa(a: list[int], b: list[int], weights: str | None = None) -> float:
    cats = list(range(1, 6))
    n = len(a)
    obs = Counter(zip(a, b))
    pa, pb = Counter(a), Counter(b)

    def w(i, j):
        if weights == "quadratic":
            return (i - j) ** 2 / 16
        return 0.0 if i == j else 1.0
    do = sum(w(i, j) * obs[(i, j)] / n for i in cats for j in cats)
    de = sum(w(i, j) * pa[i] * pb[j] / n / n for i in cats for j in cats)
    return round(1 - do / de, 4) if de else 1.0


async def score() -> None:
    from agent.llm import model_for
    from evals.runners.judge import judge
    rows = list(csv.DictReader(LABELS.open()))
    rows = [r for r in rows if r["my_score"].strip()]
    if len(rows) < 30:
        raise SystemExit(f"only {len(rows)} labelled items; the brief asks for 30")
    me, jd, detail = [], [], []
    for r in rows:
        v = await judge(job_text(seed_jobs()[r["job_id"]]), r["proposal"])
        me.append(int(r["my_score"]))
        jd.append(v.score)
        detail.append({"item_id": r["item_id"], "mine": int(r["my_score"]), "judge": v.score, "judge_reason": v.reason,
                       "my_note": r["my_note"]})
    exact = sum(x == y for x, y in zip(me, jd)) / len(me)
    within1 = sum(abs(x - y) <= 1 for x, y in zip(me, jd)) / len(me)
    write_result("judge_calibration", {
        "judge_model": model_for("judge")["model"], "n": len(me), "cohen_kappa": kappa(me, jd),
        "quadratic_weighted_kappa": kappa(me, jd, "quadratic"), "exact_agreement": round(exact, 4),
        "within_one": round(within1, 4), "mean_mine": round(sum(me) / len(me), 3), "mean_judge": round(sum(jd) / len(jd), 3),
        "disagreements": sorted([d for d in detail if d["mine"] != d["judge"]], key=lambda d: -abs(d["mine"] - d["judge"])),
        "detail": detail})
    print(f"kappa={kappa(me, jd)} weighted={kappa(me, jd, 'quadratic')} exact={exact:.2f}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("step", choices=["prepare", "score"])
    ap.add_argument("--n", type=int, default=30)
    ap.add_argument("--sources", nargs="*", default=["outcomes_v0", "outcomes_best"])
    a = ap.parse_args()
    if a.step == "prepare":
        prepare(a.n, a.sources)
    else:
        asyncio.run(score())
