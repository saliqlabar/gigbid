"""Run the Job Analyst on dev jobs and cache the JobSpecs (requirements feed RAG variant V1).

    python -m evals.runners.build_jobspecs [--all] [--concurrency 3]
Default: the bid jobs that have gold evidence (what rag_eval scores). Resumable: done jobs are skipped.
Writes evals/results/jobspecs_<persona>.jsonl with latency and tokens per job.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import time

from agent.llm import usage_scope
from agent.nodes.analyst import analyse
from evals.runners.common import PERSONAS, RESULTS, dev_set, seed_jobs


async def one(job: dict, sem: asyncio.Semaphore) -> dict:
    async with sem:
        t = time.perf_counter()
        with usage_scope() as recs:
            spec = await analyse(job)
        return {"job_id": job["job_id"], "requirements": spec.requirements, "spec": spec.model_dump(),
                "latency_s": round(time.perf_counter() - t, 2),
                "tokens": sum(r["tokens_in"] + r["tokens_out"] for r in recs),
                "model": recs[-1]["model"] if recs else None}


async def main(a) -> None:
    sem = asyncio.Semaphore(a.concurrency)
    jobs = seed_jobs()
    for persona in PERSONAS:
        path = RESULTS / f"jobspecs_{persona}.jsonl"
        done = {json.loads(x)["job_id"] for x in path.read_text().splitlines()} if path.exists() else set()
        rows = [r for r in dev_set(persona) if a.all or (r["bid"] and r["gold_evidence"])]
        todo = [jobs[r["job_id"]] for r in rows if r["job_id"] not in done]
        print(f"{persona}: {len(done)} cached, {len(todo)} to run", flush=True)
        for i in range(0, len(todo), 12):
            batch = await asyncio.gather(*[one(j, sem) for j in todo[i:i + 12]], return_exceptions=True)
            with path.open("a") as f:
                for b in batch:
                    if isinstance(b, dict):
                        f.write(json.dumps(b) + "\n")
                    else:
                        print("  error:", b, flush=True)
            print(f"  {persona}: {min(i + 12, len(todo))}/{len(todo)}", flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--concurrency", type=int, default=3)
    asyncio.run(main(ap.parse_args()))
