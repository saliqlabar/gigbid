"""R&D question C: is the multi-agent design worth it?

Same 50 dev jobs (stratified by job type, fixed seed), three systems:
  react      - one ReAct agent (langgraph.prebuilt.create_react_agent) holding all read tools
  supervisor - prebuilt supervisor (langgraph-supervisor) over analyst / risk / fit / writer agents
  graph      - GigBid's hand-built bid graph (agent/graphs/bid_graph.py)
All three get the same MCP tools (in-process), the same strong model for the baselines, and the same
harness "approval": a bid letter is submitted to GigSim unless the system itself blocked it.
Metrics: bid-or-skip accuracy and F1, scam recall, unsupported claims per proposal (one independent
verifier pass on the FINAL letter against the evidence that system actually saw), compliance violations
left in the final letter (Compliance Guard rules applied after the fact) and caught by the system,
GigSim reply rate, tokens and latency per job.

    python -m evals.runners.study_c --systems react supervisor graph --jobs 50
Writes evals/results/study_c.json and study_c_detail.jsonl (resumable).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import time
from collections import defaultdict

from evals.runners import harness
from evals.runners.common import PERSONAS, RESULTS, percentile, seed_jobs, write_result
from evals.runners.decision_eval import prf, sample


def jobs_for_study(total: int) -> list[tuple[str, dict]]:
    per = [total // 3 + (1 if i < total % 3 else 0) for i in range(3)]
    return [(p, r) for p, n in zip(PERSONAS, per) for r in sample(p, n)]


def _chat(tier: str = "strong"):
    import os

    from langchain_openai import ChatOpenAI

    from agent.llm import _mcfg, model_for
    spec = model_for("baseline", tier)
    prov = _mcfg()["providers"][spec["provider"]]
    kw = {}
    if spec.get("think") is False and spec["provider"] == "ollama":
        kw["extra_body"] = {"think": False}
    if spec.get("reasoning_effort"):
        kw["reasoning_effort"] = spec["reasoning_effort"]
    return ChatOpenAI(model=spec["model"], base_url=os.getenv(f"{spec['provider'].upper()}_BASE_URL", prov["base_url"]),
                      api_key=os.getenv(prov.get("api_key_env", ""), "") or "not-needed", temperature=0.1,
                      timeout=180, max_retries=1, **kw), spec


async def mcp_tools(node: str, layer, seen: dict) -> list:
    """LangChain tools over the real MCP servers (in-process), restricted by the node's allowlist.
    Portfolio results are recorded in `seen` so verification uses exactly the evidence the agent saw."""
    from langchain_core.tools import StructuredTool
    from mcp.shared.memory import create_connected_server_and_client_session as connect

    from agent.mcp_client import allowlist
    from mcp_servers.marketplace import server as m
    from mcp_servers.portfolio import server as p
    allowed = set(allowlist(node))
    out = []
    for srv, mod in (("marketplace", m), ("portfolio", p)):
        async with connect(mod.mcp._mcp_server) as c:
            listed = (await c.list_tools()).tools
        for t in listed:
            q = f"{srv}.{t.name}"
            if q not in allowed:
                continue

            async def fn(_q=q, **kw):
                try:
                    res = await layer.call(node, _q, kw)
                except Exception as e:  # noqa: BLE001 - the agent sees the error, as with any tool
                    return f"ERROR: {e}"
                if _q == "portfolio.search_evidence":
                    for r in res.get("results", []):
                        seen[r["evidence_id"]] = r.get("snippet", "")
                elif _q == "portfolio.get_case_study":
                    seen[res.get("evidence_id", "")] = res.get("text", "")[:1200]
                return json.dumps(res, default=str)[:3000]
            out.append(StructuredTool(name=t.name, description=t.description or "", args_schema=t.inputSchema,
                                      coroutine=fn))
    return out


def parse_final(text: str) -> dict:
    from agent.llm import _extract_json
    try:
        d = json.loads(_extract_json(text or ""))
        return d if isinstance(d, dict) else {}
    except ValueError:
        return {}


async def run_baseline(system: str, persona: str, row: dict) -> dict:
    from langchain_core.callbacks import get_usage_metadata_callback
    from langgraph.prebuilt import create_react_agent

    from agent.mcp_client import ToolLayer
    from agent.prompts import render
    from agent.runner import profile_for
    prof = profile_for(persona)
    job = seed_jobs()[row["job_id"]]
    job_block = json.dumps({k: job.get(k) for k in ("job_id", "title", "description", "category", "skills", "budget_type",
                                                    "budget_max", "hourly_low", "hourly_high", "client_id",
                                                    "screening_questions", "connects_cost")}, default=str)
    variables = {"profile": json.dumps(prof)[:800], "job": job_block, "max_words": 250}
    layer = ToolLayer("eval", persona, f"tr_c_{system}_{row['job_id']}")
    seen: dict[str, str] = {}
    model, spec = _chat()
    t = time.perf_counter()
    with get_usage_metadata_callback() as cb:
        if system == "react":
            sys_p, user_p, _ = render("baseline_react", variables)
            agent = create_react_agent(model, await mcp_tools("baseline_react", layer, seen), prompt=sys_p)
        else:
            from langgraph_supervisor import create_supervisor
            sys_p, user_p, _ = render("baseline_supervisor", variables)
            w_sys, _, _ = render("baseline_workers", variables)
            wp = dict(line.split(": ", 1) for line in w_sys.splitlines() if ": " in line)
            workers = [create_react_agent(model, [], prompt=wp["analyst"], name="analyst"),
                       create_react_agent(model, await mcp_tools("baseline_risk", layer, seen), prompt=wp["risk"], name="risk"),
                       create_react_agent(model, await mcp_tools("baseline_fit", layer, seen), prompt=wp["fit"], name="fit"),
                       create_react_agent(model, [], prompt=wp["writer"], name="writer")]
            agent = create_supervisor(workers, model=model, prompt=sys_p, output_mode="full_history").compile()
        try:
            res = await asyncio.wait_for(agent.ainvoke({"messages": [("user", user_p)]}, {"recursion_limit": 40}), 600)
            final = parse_final(res["messages"][-1].content)
            steps = len(res["messages"])
            err = None if final else "final answer was not JSON"
        except Exception as e:  # noqa: BLE001 - recursion limit, timeout, model errors count as failures
            final, steps, err = {}, None, f"{type(e).__name__}: {str(e)[:200]}"
        usage = cb.usage_metadata
    tokens = sum(u.get("total_tokens", 0) for u in usage.values())
    return {"decision": final.get("decision"), "scam": bool(final.get("scam")),
            "injection_detected": bool(final.get("injection_detected")), "bid_amount": final.get("bid_amount"),
            "bid_type": final.get("bid_type") or ("hourly" if job.get("budget_type") == "hourly" else "fixed"),
            "cover_letter": final.get("cover_letter") or "", "evidence": seen, "blocked": False,
            "violations_caught": 0, "tokens": tokens, "latency_s": round(time.perf_counter() - t, 2),
            "steps": steps, "error": err, "model": spec["model"]}


async def run_graph(g, persona: str, row: dict) -> dict:
    from agent.runner import profile_for
    tid = f"c-{persona}-{row['job_id']}"
    cfg = {"configurable": {"thread_id": tid}, "recursion_limit": 80}
    t = time.perf_counter()
    await g.ainvoke({"thread_id": tid, "user_id": "eval", "persona": persona, "trace_id": f"tr_c_graph_{row['job_id']}",
                     "job": {"job_id": row["job_id"]}, "profile": profile_for(persona), "status": "analysing",
                     "revisions": 0}, cfg)
    snap = await g.aget_state(cfg)
    s = snap.values
    paused = any(tk.interrupts for tk in snap.tasks)
    comp = s.get("compliance_verdict") or {}
    rec = {"decision": (s.get("decision") or {}).get("action"), "scam": bool((s.get("risk_report") or {}).get("scam")),
           "injection_detected": bool((s.get("risk_report") or {}).get("injection_detected")),
           "evidence": {e["evidence_id"]: e["snippet"] for e in s.get("evidence", [])}, "cover_letter": "",
           "blocked": paused and not comp.get("allowed", True), "violations_caught": len(comp.get("violations", [])),
           "steps": len(s.get("usage", [])), "error": None, "model": "graph (cheap+strong by node)"}
    if paused:
        draft = s["proposal_draft"]
        rec["cover_letter"] = (comp.get("revised_text") or draft["cover_letter"]) if comp else draft["cover_letter"]
        price = s.get("price_quote") or {}
        rec.update(bid_amount=price.get("amount"), bid_type=price.get("bid_type"))
    rec.update(tokens=s.get("tokens", 0), latency_s=round(time.perf_counter() - t, 2))
    return rec


async def submit(adapter, rec: dict) -> str | None:
    """The harness plays the approving human for every system: a drafted, unblocked letter is submitted."""
    system, persona, row = rec["system"], rec["persona"], {"job_id": rec["job_id"]}
    if rec["decision"] != "bid" or not rec["cover_letter"] or rec.get("blocked"):
        return None
    from agent.nodes.approval import client_text
    job = seed_jobs()[row["job_id"]]
    amount = rec.get("bid_amount")
    try:
        amount = float(amount)
    except (TypeError, ValueError):
        amount = float(job.get("budget_max") or job.get("hourly_high") or 50)
    try:
        d = await adapter.create_proposal_draft({"job_id": row["job_id"], "freelancer_id": persona,
                                                 "cover_letter": client_text(rec["cover_letter"]), "bid_amount": amount,
                                                 "bid_type": rec.get("bid_type") or "fixed", "screening_answers": []},
                                                f"c-{system}-d-{row['job_id']}")
        await adapter.submit_proposal(d["proposal_id"], f"c-{system}-s-{row['job_id']}")
        return d["proposal_id"]
    except Exception as e:  # noqa: BLE001
        rec["error"] = f"submit failed: {e}"
        return None


async def score_letter(rec: dict, policy: dict) -> None:
    from agent.nodes.compliance import check
    from rag.verify import verify_text
    if not rec["cover_letter"]:
        rec.update(unsupported=0, claims=0, violations_left=0)
        return
    v = await verify_text(rec["cover_letter"], rec["evidence"])
    rec["claims"] = sum(c.status != "not_a_claim" for c in v.claims)
    rec["unsupported"] = sum(c.status == "unsupported" for c in v.claims)
    rec["violations_left"] = len([x for x in check(rec["cover_letter"], policy).violations
                                  if x.rule != "no_fabricated_experience"])


def summarise(rows: list[dict]) -> dict:
    ok = [r for r in rows if r["decision"]]
    bid = lambda r: r["decision"] == "bid"  # noqa: E731
    tp = sum(bid(r) and r["gold_bid"] for r in ok)
    fp = sum(bid(r) and not r["gold_bid"] for r in ok)
    fn = sum(not bid(r) and r["gold_bid"] for r in ok)
    _, _, f1 = prf(tp, fp, fn)
    scams = [r for r in rows if r["gold_scam"]]
    props = [r for r in rows if r["cover_letter"]]
    sub = [r for r in rows if r.get("outcome")]
    replied = [r for r in sub if r["outcome"] in ("replied", "interview", "hired")]
    lat = [r["latency_s"] for r in rows]
    return {"jobs": len(rows), "completed": len(ok), "failed": len(rows) - len(ok),
            "bid_skip_accuracy": round(sum(bid(r) == r["gold_bid"] for r in rows) / len(rows), 4) if rows else None,
            "bid_f1": f1, "scam_recall": round(sum(r["scam"] for r in scams) / len(scams), 4) if scams else None,
            "scams_bid_on": sum(bid(r) for r in scams),
            "proposals": len(props), "unsupported_per_proposal": round(sum(r["unsupported"] for r in props) / len(props), 3)
            if props else None,
            "violations_left_in_final": sum(r["violations_left"] for r in props),
            "violations_caught": sum(r["violations_caught"] for r in rows),
            "submitted": len(sub), "reply_rate": round(len(replied) / len(sub), 4) if sub else None,
            "tokens_per_job": round(sum(r["tokens"] for r in rows) / len(rows)) if rows else None,
            "latency_p50_s": percentile(lat, .5), "latency_p95_s": percentile(lat, .95)}


async def main(a) -> None:
    adapter = harness.fresh_gigsim()
    g, _ = harness.full_graph()
    policy = await adapter.get_policy()
    detail = RESULTS / f"{a.out}_detail.jsonl"
    done = {}
    if detail.exists() and not a.fresh:
        for line in detail.read_text().splitlines():
            r = json.loads(line)
            done[(r["system"], r["job_id"])] = r
    study = jobs_for_study(a.jobs)
    for system in a.systems:
        todo = [(p, r) for p, r in study if (system, r["job_id"]) not in done]
        print(f"{system}: {len(todo)} to run", flush=True)
        for i, (persona, row) in enumerate(todo):
            try:
                rec = await (run_graph(g, persona, row) if system == "graph" else run_baseline(system, persona, row))
            except Exception as e:  # noqa: BLE001
                rec = {"decision": None, "scam": False, "injection_detected": False, "cover_letter": "", "evidence": {},
                       "blocked": False, "violations_caught": 0, "tokens": 0, "latency_s": 0, "steps": None,
                       "error": f"{type(e).__name__}: {str(e)[:200]}"}
            await score_letter(rec, policy)
            rec.update(system=system, persona=persona, job_id=row["job_id"], job_type=row["job_type"],
                       gold_bid=row["bid"], gold_scam=row["scam"], gold_injection=row["injection"])
            rec.pop("evidence", None)
            with detail.open("a") as f:
                f.write(json.dumps(rec, default=str) + "\n")
            done[(system, row["job_id"])] = rec
            print(f"  {system} {i + 1}/{len(todo)} {row['job_id']} -> {rec['decision']} ({rec['latency_s']}s)"
                  f"{' ERR ' + rec['error'] if rec.get('error') else ''}", flush=True)
    # Outcomes: submit every system's approved letters into one fresh GigSim with deterministic keys, then let
    # the simulated clients decide (rule mode). Doing this at the end keeps the run resumable.
    adapter = harness.fresh_gigsim()
    rows = [r for r in done.values() if r["system"] in a.systems]
    for r in rows:
        r["proposal_id"] = await submit(adapter, r)
    from gigsim import app as gapp
    gapp.tick()
    outcomes = {}
    for p in PERSONAS:
        for pr in await adapter.list_proposals(p):
            outcomes[pr["proposal_id"]] = pr["status"]
    for r in rows:
        r["outcome"] = outcomes.get(r.get("proposal_id") or "")
    by = defaultdict(list)
    for r in rows:
        by[r["system"]].append(r)
    from agent.llm import model_for
    out = {"jobs": a.jobs, "baseline_model": model_for("baseline", "strong")["model"],
           "graph_models": {"cheap": model_for("analyst")["model"], "strong": model_for("writer")["model"]},
           "results": {s: summarise(v) for s, v in by.items()},
           "by_job_type": {s: {t: summarise([r for r in v if r["job_type"] == t]) for t in sorted({r["job_type"] for r in v})}
                           for s, v in by.items()},
           "detail_file": str(detail.relative_to(RESULTS.parent.parent))}
    print(json.dumps(out["results"], indent=1))
    write_result(a.out, out)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--systems", nargs="*", default=["react", "supervisor", "graph"])
    ap.add_argument("--jobs", type=int, default=50)
    ap.add_argument("--out", default="study_c")
    ap.add_argument("--fresh", action="store_true")
    asyncio.run(main(ap.parse_args()))
