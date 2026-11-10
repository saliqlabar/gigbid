"""Decision evaluation (B4): bid-or-skip F1, scam precision/recall, injection detection, price inside the
fair band, p50/p95 latency, tokens and USD per job analysed. Runs the production bid graph up to the
decision + pricing (drafting is not needed for these metrics).

    python -m evals.runners.decision_eval --per-persona 40 [--out decisions]
Sample: stratified by job_type per persona with a fixed seed, so reruns use the same jobs.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import random
import time
from collections import Counter, defaultdict

from evals.runners import harness
from evals.runners.common import PERSONAS, RESULTS, dev_set, percentile, write_result


def sample(persona: str, n: int, seed: int = 4016) -> list[dict]:
    rows = dev_set(persona)
    by = defaultdict(list)
    for r in rows:
        by[r["job_type"]].append(r)
    rng = random.Random(seed)
    for v in by.values():
        rng.shuffle(v)
    out, types = [], sorted(by)
    while len(out) < min(n, len(rows)):            # round-robin over job types = stratified
        for t in types:
            if by[t] and len(out) < n:
                out.append(by[t].pop())
    return out


async def run_one(g, row: dict, persona: str, sem) -> dict:
    from agent.runner import profile_for
    async with sem:
        tid = f"eval-{persona}-{row['job_id']}"
        cfg = {"configurable": {"thread_id": tid}, "recursion_limit": 60}
        t = time.perf_counter()
        inp = {"thread_id": tid, "user_id": "eval", "persona": persona, "trace_id": f"tr_eval_{row['job_id']}",
               "job": {"job_id": row["job_id"], "duplicate_of": row.get("duplicate_of")}, "profile": profile_for(persona),
               "status": "analysing", "revisions": 0}
        await g.ainvoke(inp, cfg, interrupt_before=["writer"])
        s = (await g.aget_state(cfg)).values
        return {"job_id": row["job_id"], "persona": persona, "job_type": row["job_type"], "gold_bid": row["bid"],
                "gold_scam": row["scam"], "gold_injection": row["injection"], "skip_reason": row["skip_reason"],
                "pred": (s.get("decision") or {}).get("action"), "rule": (s.get("decision") or {}).get("rule"),
                "status": s.get("status"), "fit": (s.get("fit_report") or {}).get("score"),
                "risk": (s.get("risk_report") or {}).get("score"), "pred_scam": (s.get("risk_report") or {}).get("scam"),
                "pred_injection": (s.get("risk_report") or {}).get("injection_detected"),
                "price": s.get("price_quote"), "band": row["fair_price_band"], "errors": s.get("errors", []),
                "latency_s": round(time.perf_counter() - t, 2), "tokens": s.get("tokens", 0), "cost_usd": s.get("cost_usd", 0),
                "models": sorted({c["model"] for u in s.get("usage", []) for c in u["llm_calls"]})}


def prf(tp, fp, fn):
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    return round(p, 4), round(r, 4), round(2 * p * r / (p + r), 4) if p + r else 0.0


def metrics(rows: list[dict]) -> dict:
    ok = [r for r in rows if r["pred"]]
    bid = lambda r: r["pred"] == "bid"  # noqa: E731 - watch counts as "not bid"
    tp = sum(bid(r) and r["gold_bid"] for r in ok)
    fp = sum(bid(r) and not r["gold_bid"] for r in ok)
    fn = sum(not bid(r) and r["gold_bid"] for r in ok)
    p, rcl, f1 = prf(tp, fp, fn)
    stp = sum(bool(r["pred_scam"]) and r["gold_scam"] for r in ok)
    sfp = sum(bool(r["pred_scam"]) and not r["gold_scam"] for r in ok)
    sfn = sum(not r["pred_scam"] and r["gold_scam"] for r in ok)
    sp, sr, sf = prf(stp, sfp, sfn)
    inj = [r for r in ok if r["gold_injection"]]
    priced = [r for r in ok if r["price"] and r["gold_bid"] and r["band"]["type"] == r["price"]["bid_type"]]
    in_band = [r for r in priced if r["band"]["low"] - 1e-6 <= r["price"]["amount"] <= r["band"]["high"] + 1e-6]
    lat = [r["latency_s"] for r in ok]
    return {"n": len(rows), "completed": len(ok), "bid_precision": p, "bid_recall": rcl, "bid_f1": f1,
            "accuracy": round(sum(bid(r) == r["gold_bid"] for r in ok) / len(ok), 4) if ok else None,
            "scam_precision": sp, "scam_recall": sr, "scam_f1": sf, "scam_n": stp + sfn,
            "injection_detected_rate": round(sum(bool(r["pred_injection"]) for r in inj) / len(inj), 4) if inj else None,
            "injection_n": len(inj),
            "price_in_band": round(len(in_band) / len(priced), 4) if priced else None, "priced_n": len(priced),
            "latency_p50_s": percentile(lat, .5), "latency_p95_s": percentile(lat, .95),
            "tokens_per_job": round(sum(r["tokens"] for r in ok) / len(ok)) if ok else None,
            "usd_per_job": round(sum(r["cost_usd"] for r in ok) / len(ok), 6) if ok else None,
            "pred_actions": dict(Counter(r["pred"] for r in ok)), "rules": dict(Counter(r["rule"] for r in ok))}


async def main(a) -> None:
    harness.fresh_gigsim()
    g, _ = harness.analysis_graph()
    sem = asyncio.Semaphore(a.concurrency)
    detail_path = RESULTS / f"{a.out}_detail.jsonl"
    done = {}
    if detail_path.exists() and not a.fresh:
        done = {json.loads(x)["job_id"] + json.loads(x)["persona"]: json.loads(x) for x in detail_path.read_text().splitlines()}
    rows = []
    for persona in PERSONAS:
        todo = [r for r in sample(persona, a.per_persona) if r["job_id"] + persona not in done]
        rows += [d for d in done.values() if d["persona"] == persona]
        print(f"{persona}: {len(todo)} to run", flush=True)
        for i in range(0, len(todo), 9):
            batch = await asyncio.gather(*[run_one(g, r, persona, sem) for r in todo[i:i + 9]], return_exceptions=True)
            with detail_path.open("a") as f:
                for b in batch:
                    if isinstance(b, dict):
                        f.write(json.dumps(b, default=str) + "\n")
                        rows.append(b)
                    else:
                        print("  error:", repr(b)[:200], flush=True)
            print(f"  {persona}: {min(i + 9, len(todo))}/{len(todo)}", flush=True)
    import os

    from agent.llm import model_for, profile_name
    out = {"profile": profile_name(), "models": {"cheap": model_for("analyst")["model"], "strong": model_for("writer")["model"]},
           "hardware": os.getenv("GIGBID_HARDWARE", "RTX 4060 Laptop 8 GB, WSL2 (local Ollama)"),
           "per_persona_sample": a.per_persona, "overall": metrics(rows),
           "by_persona": {p: metrics([r for r in rows if r["persona"] == p]) for p in PERSONAS},
           "by_job_type": {t: metrics([r for r in rows if r["job_type"] == t]) for t in sorted({r["job_type"] for r in rows})},
           "detail_file": str(detail_path.relative_to(RESULTS.parent.parent))}
    print(json.dumps(out["overall"], indent=1))
    write_result(a.out, out)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-persona", type=int, default=40)
    ap.add_argument("--concurrency", type=int, default=3)
    ap.add_argument("--out", default="decisions")
    ap.add_argument("--fresh", action="store_true")
    asyncio.run(main(ap.parse_args()))
