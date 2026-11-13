"""Export one graph thread's execution trace as a public, readable file (README "three public traces", C).

    python scripts/docs/export_trace.py <thread_id> <name> [--title "..."]
Reads the run-event log (agent/trace.py: every node start/end, tool call with arguments and result summary,
LLM call with model, tokens, cost and latency) and the thread's final checkpointed state, and writes
docs/traces/<name>.md (timeline) and docs/traces/<name>.json (raw events). PII is masked again on export.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from agent import db
from agent.safety.pii import mask

OUT = Path(__file__).resolve().parents[2] / "docs" / "traces"


async def final_state(thread_id: str) -> dict:
    from agent.persistence import open_persistence
    from agent.runner import new_conversation_graph, new_graph
    async with open_persistence() as (cp, store):
        g = new_conversation_graph(cp, store) if thread_id.startswith("conv-") else new_graph(cp, store)
        snap = await g.aget_state({"configurable": {"thread_id": thread_id}})
        return {"values": snap.values, "next": list(snap.next),
                "interrupts": [i.value for t in snap.tasks for i in (t.interrupts or [])]}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("thread_id")
    ap.add_argument("name")
    ap.add_argument("--title", default="")
    a = ap.parse_args()
    rows = db.query("select ts, node, kind, data, trace_id from run_events where thread_id=? order by ts", (a.thread_id,))
    if not rows:
        raise SystemExit(f"no run events for {a.thread_id}")
    events = [{"ts": r["ts"], "node": r["node"], "kind": r["kind"], "data": mask(json.loads(r["data"] or "{}")),
               "trace_id": r["trace_id"]} for r in rows]
    st = asyncio.run(final_state(a.thread_id))
    v = st["values"]
    t0 = events[0]["ts"]
    lines = [f"# Trace: {a.title or a.name}", "",
             f"Thread `{a.thread_id}` · trace id `{events[0]['trace_id']}` · job `{(v.get('job') or {}).get('job_id')}` "
             f"\"{(v.get('job') or {}).get('title', '')}\"", "",
             f"Final status **{v.get('status')}** · decision **{(v.get('decision') or {}).get('action')}** "
             f"(rule `{(v.get('decision') or {}).get('rule')}`) · tokens {v.get('tokens')} · cost {v.get('cost_usd')} USD · "
             f"steps {v.get('steps')} · critic revisions {v.get('revisions', 0)}", "",
             "| t (s) | node | event | detail |", "|---|---|---|---|"]
    for e in events:
        d = e["data"]
        if e["kind"] == "tool":
            detail = f"`{d.get('tool')}` {json.dumps(d.get('args'))[:120]} → {'ok' if d.get('ok') else 'ERROR ' + str(d.get('error'))[:80]}"
        elif e["kind"] == "end":
            detail = f"{d.get('latency_s')} s · {d.get('tokens')} tokens · {d.get('cost_usd')} USD" + \
                (f" · status {d['status']}" if d.get("status") else "")
        else:
            detail = json.dumps(d)[:160]
        lines.append(f"| {e['ts'] - t0:.1f} | {e['node']} | {e['kind']} | {detail.replace('|', '/')} |")
    lines += ["", "## Model calls per node (tokens and cost per node, R8)", "",
              "| node | model | tier | tokens in | tokens out | latency s | USD | prompt version |", "|---|---|---|---|---|---|---|---|"]
    for u in v.get("usage", []):
        for c in u.get("llm_calls", []):
            lines.append(f"| {u['node']} | {c.get('model')} | {c.get('tier')} | {c.get('tokens_in')} | {c.get('tokens_out')} | "
                         f"{c.get('latency_s')} | {c.get('cost_usd')} | {c.get('prompt_version')} |")
    if st["interrupts"]:
        lines += ["", "## Pending approval payload (what the human sees)", "", "```json",
                  json.dumps(mask(st["interrupts"][0]), indent=1, default=str)[:6000], "```"]
    for k in ("risk_report", "fit_report", "decision", "price_quote", "critiques", "compliance_verdict", "submission"):
        if v.get(k):
            lines += ["", f"## {k}", "", "```json", json.dumps(mask(v[k]), indent=1, default=str)[:3000], "```"]
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{a.name}.md").write_text("\n".join(lines) + "\n")
    (OUT / f"{a.name}.json").write_text(json.dumps({"thread_id": a.thread_id, "events": events}, indent=1, default=str))
    print(f"wrote docs/traces/{a.name}.md ({len(events)} events)")


if __name__ == "__main__":
    main()
