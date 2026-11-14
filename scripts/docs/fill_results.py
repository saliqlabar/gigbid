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


def block_rag_personas() -> str:
    d = load("rag_variants")
    if not d:
        return pending("evals/results/rag_variants.json")
    rows = []
    for v, r in d["results"].items():
        for p, pr in r["personas"].items():
            jt = pr.get("recall@5_by_job_type", {})
            rows.append([v, p, pr["n"], f(pr["recall@5"]), f(pr["ndcg@10"]),
                         f(jt.get("1-3 requirements", {}).get("recall@5")), f(jt.get("4+ requirements", {}).get("recall@5"))])
    return table(["Variant", "Persona", "Jobs", "R@5", "nDCG@10", "R@5, 1-3 requirements", "R@5, 4+ requirements"], rows,
                 "`evals/results/rag_variants.json` (`personas.*.recall@5_by_job_type`)")


def block_ablation() -> str:
    d = load("rag_ablation")
    if not d:
        return pending("evals/results/rag_ablation.json")
    rows = [[v, f(r["overall"]["recall@5"]), f(r["overall"]["mrr"]), f(r["overall"]["ndcg@10"])] for v, r in d["results"].items()]
    return table(["Configuration", "R@5", "MRR", "nDCG@10"], rows, "`evals/results/rag_ablation.json`")


def block_change_request() -> str:
    d = load("change_request")
    if not d:
        return pending("evals/results/change_request.json")
    p, c, e = d["pricing"], d["compliance"], d["code_edit"]
    rows = [["GigSim served policy 1.1 unchanged", d["served_unchanged"]],
            ["Code commit / files", f"`{e['commit']}` · " + "; ".join(x.strip() for x in e["files"][:-1])],
            ["Bids re-priced (from `" + p["source"] + "`)", p["bids"]],
            ["Connects spent, policy 1.0 → 1.1", f"{p['connects_spent_v10']} → {p['connects_spent_v11']}"],
            ["Hourly bids raised to the $10/h minimum", f"{p['hourly_bids_raised_to_minimum']} of {p['hourly_bids']}"],
            ["Mean expected value per bid (USD), 1.0 → 1.1", f"{f(p['mean_ev_v10'], 2)} → {f(p['mean_ev_v11'], 2)}"],
            ["Bids no longer worth the Connects", p["flipped_to_not_worth_it"]],
            ["Drafted letters re-checked", c["letters"]],
            ["Blocked by Compliance Guard, 1.0 → 1.1", f"{c['blocked_v10']} → {c['blocked_v11']}"],
            ["Newly blocked by the 300-word limit", c["newly_blocked_by_300_word_limit"]]]
    return table(["", "Value"], rows, "`evals/results/change_request.json`")


def block_failures() -> str:
    d = load("failure_analysis")
    if not d:
        return pending("evals/results/failure_analysis.json")
    fx = d["fix_72d8bb0_same_jobs"]
    rows = [[k, n, fx["by_category"].get(k, {}).get("before", "n/a"), fx["by_category"].get(k, {}).get("after", "n/a"),
             fx["by_category"].get(k, {}).get("fixed", "n/a")] for k, n in d["by_category"].items()]
    return table(["Category", "Failures (final runs)", "Decision failures before fix", "After fix (same jobs)", "Fixed"], rows,
                 f"`evals/results/failure_analysis.json` ({d['n_failures']} failures on {d['n_failed_jobs']} jobs; "
                 f"fix comparison on {fx['jobs_compared']} jobs common to both decision runs)")


def block_budgets() -> str:
    import yaml
    decl = yaml.safe_load((ROOT / "agent" / "config.yaml").read_text())["budgets"]["declared"]
    rows = []
    for env, dec_name, out_name in (("local", "decisions_open", "outcomes_best"), ("hosted", "decisions_hosted", "outcomes_hosted")):
        d, o, b = load(dec_name), load(out_name), decl[env]
        dj = d["overall"] if d else {}
        oj = o["overall"] if o else {}
        def chk(m, lim):
            return "n/a" if m is None else ("met" if m <= lim else "**missed**")
        rows.append([env, "p95 s per job analysed", b["p95_s_per_job_analysed"], f(dj.get("latency_p95_s"), 1),
                     chk(dj.get("latency_p95_s"), b["p95_s_per_job_analysed"])])
        rows.append([env, "p95 s per proposal", b["p95_s_per_proposal"], f(oj.get("latency_p95_s"), 1),
                     chk(oj.get("latency_p95_s"), b["p95_s_per_proposal"])])
        rows.append([env, "USD per job analysed", b["usd_per_job_analysed"], f(dj.get("usd_per_job"), 4),
                     chk(dj.get("usd_per_job"), b["usd_per_job_analysed"])])
        rows.append([env, "USD per proposal", b["usd_per_proposal"], f(oj.get("usd_per_proposal"), 4),
                     chk(oj.get("usd_per_proposal"), b["usd_per_proposal"])])
    return table(["Environment", "Budget", "Declared", "Measured", "Status"], rows,
                 "declared in `agent/config.yaml → budgets.declared`; measured from `decisions_open.json`, `outcomes_best.json` "
                 "(local) and `decisions_hosted.json`, `outcomes_hosted.json` (hosted, n/a if not run)")


BLOCKS = {"rag_personas": block_rag_personas, "ablation": block_ablation, "change_request": block_change_request,
          "failures": block_failures, "budgets": block_budgets, "rag": block_rag, "tools": block_tools, "decisions": block_decisions, "study_c": block_study_c,
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
    for p in [ROOT / "README.md", *sorted((ROOT / "docs").glob("*.md")), *sorted((ROOT / "docs" / "report").glob("*.md"))]:
        if (k := fill(p)):
            print(f"{p.relative_to(ROOT)}: {k} block(s)")
