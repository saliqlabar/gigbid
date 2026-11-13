"""Fill the generated results blocks in README.md and docs/*.md from evals/results/*.json.

    python scripts/docs/fill_results.py

A block is `<!-- RESULTS:<name> ... -->` ... `<!-- /RESULTS:<name> -->`; everything between the markers is
replaced. Numbers are never typed by hand: each table names the file it came from, and a block whose file does
not exist yet says so instead of showing a number.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RES = ROOT / "evals" / "results"


def load(name: str) -> dict | None:
    p = RES / f"{name}.json"
    return json.loads(p.read_text()) if p.exists() else None


def f(v, nd=3, pct=False) -> str:
    if v is None:
        return "n/a"
    if pct:
        return f"{100 * v:.1f}%"
    return f"{v:.{nd}f}" if isinstance(v, float) else str(v)


def table(head: list[str], rows: list[list], src: str) -> str:
    out = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(out) + f"\n\n<sub>Source: {src}</sub>"


def pending(src: str) -> str:
    return f"*Pending: `{src}` has not been produced yet.*"


def block_rag() -> str:
    d, out_v0, out_best = load("rag_variants"), load("outcomes_v0"), load("outcomes_best")
    if not d:
        return pending("evals/results/rag_variants.json")
    outs = {}
    for o in (out_v0, out_best):
        if o:
            outs[o["rag_variant"]] = o["overall"]
    rows = []
    for v, r in d["results"].items():
        o = outs.get(v, {})
        rows.append([v, f(r["overall"]["recall@5"]), f(r["overall"]["mrr"]), f(r["overall"]["ndcg@10"]),
                     f(o.get("unsupported_after_per_proposal")), f(o.get("judge_mean"), 2), f(o.get("reply_rate"), pct=True),
                     f(o.get("latency_p95_s"), 1), f(o.get("usd_per_proposal"), 4)])
    return table(["Variant", "Evidence Recall@5", "MRR", "nDCG@10", "Unsupported claims / proposal", "Judge score (1-5)",
                  "GigSim reply rate", "p95 latency (s)", "Cost / proposal (USD)"], rows,
                 "`evals/results/rag_variants.json` (all bid jobs with gold evidence, 3 personas); proposal columns from "
                 "`outcomes_v0.json` / `outcomes_best.json` (fixed set, 5 jobs per persona, local open models, 0 USD)")


def block_tools() -> str:
    a, b = load("tool_granularity_cheap_shipped"), load("tool_granularity_cheap_rewritten")
    if not a:
        return pending("evals/results/tool_granularity_cheap_shipped.json")
    rows = [[("best (descriptions as shipped)" if k == "best" else k), v["tools"], f(v["tool_selection_accuracy"]), f(v["argument_accuracy"]), v["tokens_per_task"]]
            for k, v in a["results"].items()]
    if b:
        v = b["results"]["best"]
        rows.append(["best, rewritten descriptions", v["tools"], f(v["tool_selection_accuracy"]), f(v["argument_accuracy"]),
                     v["tokens_per_task"]])
    return table(["Tool surface", "Tools", "Selection accuracy", "Argument accuracy", "Tokens / task"], rows,
                 f"`tool_granularity_cheap_shipped.json`, `tool_granularity_cheap_rewritten.json` (40 tasks, {a['model']})")


def block_decisions() -> str:
    rows = []
    for name, label in (("decisions_open", "open (qwen3:4b-instruct cheap)"), ("decisions_open_alt", "open_alt (llama3.2 cheap)")):
        d = load(name)
        if d:
            o = d["overall"]
            rows.append([label, o["n"], f(o["bid_f1"]), f(o["bid_precision"]), f(o["bid_recall"]), f(o["scam_precision"]),
                         f(o["scam_recall"]), f(o["price_in_band"], pct=True), f(o["latency_p50_s"], 1), f(o["latency_p95_s"], 1),
                         o["tokens_per_job"]])
    if not rows:
        return pending("evals/results/decisions_open.json")
    return table(["Model configuration", "Jobs", "Bid F1", "Bid P", "Bid R", "Scam P", "Scam R", "Price in fair band",
                  "p50 s / job", "p95 s / job", "Tokens / job"], rows,
                 "`decisions_open.json`, `decisions_open_alt.json` (stratified dev sample; RTX 4060 8 GB, local Ollama, 0 USD)")


def block_study_c() -> str:
    d = load("study_c")
    if not d:
        return pending("evals/results/study_c.json")
    rows = [[s, r["jobs"], r["failed"], f(r["bid_skip_accuracy"]), f(r["bid_f1"]), f(r["scam_recall"]), r["scams_bid_on"],
             f(r["unsupported_per_proposal"]), r["violations_left_in_final"], r["violations_caught"], f(r["reply_rate"], pct=True),
             r["tokens_per_job"], f(r["latency_p95_s"], 1)] for s, r in d["results"].items()]
    return table(["System", "Jobs", "Failed", "Bid/skip acc.", "Bid F1", "Scam recall", "Scams bid on",
                  "Unsupported / proposal", "Violations left", "Violations caught", "Reply rate", "Tokens / job", "p95 s"],
                 rows, f"`evals/results/study_c.json` (same {d['jobs']} dev jobs; baselines on {d['baseline_model']})")


def block_asr() -> str:
    d = load("injection_asr")
    if not d:
        return pending("evals/results/injection_asr.json")
    rows = [[lvl, r["jobs"], r["drafted"], r["flagged"], f(r["asr_first_draft_of_drafted"], pct=True), f(r["asr_sent"], pct=True),
             r["blocked"]] for lvl, r in d["results"].items()]
    return table(["Defence level", "Injection jobs", "Drafted", "Flagged", "ASR on Writer's first draft", "ASR on what would be sent",
                  "Blocked"], rows, "`evals/results/injection_asr.json`")


def block_agency() -> str:
    d = load("agency_eval")
    if not d:
        return pending("evals/results/agency_eval.json")
    rows = [["Agency routing (Fit Matcher per member)", f(d["routing_accuracy"], pct=True)],
            ["Category rule baseline", f(d["naive_category_accuracy"], pct=True)]]
    rows += [[f"  {m}: routing / category rule", f"{f(v['routing'], pct=True)} / {f(v['naive'], pct=True)}"]
             for m, v in d["by_member"].items()]
    t = table(["", f"Accuracy ({d['jobs']} jobs)"], rows, "`evals/results/agency_eval.json`")
    return t + f"\n\nConfusion (gold → routed): `{json.dumps(d['confusion'])}` · routing p95 {f(d['latency_p95_s'], 1)} s"


def block_deploy_embedding() -> str:
    a, b = load("rag_variants"), load("rag_deploy_embedding")
    if not (a and b):
        return pending("evals/results/rag_deploy_embedding.json")
    rows = []
    for v in b["results"]:
        if v in a["results"]:
            rows.append([v, f(a["results"][v]["overall"]["recall@5"]), f(b["results"][v]["overall"]["recall@5"]),
                         f(a["results"][v]["overall"]["ndcg@10"]), f(b["results"][v]["overall"]["ndcg@10"])])
    return table(["Variant", "R@5 nomic-embed (eval)", "R@5 bge-small (deploy)", "nDCG@10 nomic", "nDCG@10 bge-small"], rows,
                 "`rag_variants.json` vs `rag_deploy_embedding.json`")


def block_judge() -> str:
    d = load("judge_calibration")
    if not d:
        return pending("evals/results/judge_calibration.json")
    return (f"Cohen's kappa **{f(d['cohen_kappa'])}** (quadratic-weighted {f(d['quadratic_weighted_kappa'])}), exact agreement "
            f"{f(d['exact_agreement'], pct=True)}, within one point {f(d['within_one'], pct=True)} on {d['n']} proposals I labelled "
            f"blind (judge {d['judge_model']}). <sub>Source: `evals/results/judge_calibration.json`</sub>")


BLOCKS = {"rag": block_rag, "tools": block_tools, "decisions": block_decisions, "study_c": block_study_c,
          "asr": block_asr, "agency": block_agency, "deploy_embedding": block_deploy_embedding, "judge": block_judge}


def fill(path: Path) -> int:
    s = path.read_text()
    n = 0
    for name, fn in BLOCKS.items():
        rx = re.compile(rf"(<!-- RESULTS:{name}\b[^>]*-->)(.*?)(<!-- /RESULTS:{name} -->)", re.S)
        if rx.search(s):
            body = fn()
            s = rx.sub(lambda m, b=body: m.group(1) + "\n" + b + "\n" + m.group(3), s)
            n += 1
    path.write_text(s)
    return n


if __name__ == "__main__":
    for p in [ROOT / "README.md", *sorted((ROOT / "docs").glob("*.md"))]:
        if (k := fill(p)):
            print(f"{p.relative_to(ROOT)}: {k} block(s)")
