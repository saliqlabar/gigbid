"""Shared helpers for evaluation runners: dev set, GigSim seed jobs, metrics, results files."""
from __future__ import annotations

import json
import math
import platform
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from agent.settings import ROOT

DEV = ROOT / "evals" / "datasets"
RESULTS = ROOT / "evals" / "results"
PERSONAS = ("ai_ml", "fullstack", "data_analyst")


def dev_set(persona: str) -> list[dict]:
    return [json.loads(line) for line in (DEV / f"dev_{persona}.jsonl").read_text().splitlines() if line.strip()]


_jobs: dict[str, dict] | None = None


def seed_jobs() -> dict[str, dict]:
    """Public view of GigSim seed jobs (hidden `_` fields stripped, as the agent would see them)."""
    global _jobs
    if _jobs is None:
        raw = json.loads((ROOT / "gigsim" / "seed" / "jobs.json").read_text())
        _jobs = {j["job_id"]: {k: v for k, v in j.items() if not k.startswith("_")} for j in raw}
    return _jobs


def job_text(job: dict) -> str:
    return f"{job['title']}\n\n{job['description']}"


def recall_at(ranked: list[str], gold: set[str], k: int) -> float:
    return len(set(ranked[:k]) & gold) / min(len(gold), k) if gold else 0.0


def mrr(ranked: list[str], gold: set[str]) -> float:
    return next((1 / (i + 1) for i, e in enumerate(ranked) if e in gold), 0.0)


def ndcg_at(ranked: list[str], grades: dict[str, int], k: int) -> float:
    dcg = sum((2 ** grades.get(e, 0) - 1) / math.log2(i + 2) for i, e in enumerate(ranked[:k]))
    ideal = sorted(grades.values(), reverse=True)[:k]
    idcg = sum((2 ** g - 1) / math.log2(i + 2) for i, g in enumerate(ideal))
    return dcg / idcg if idcg else 0.0


def percentile(xs: list[float], p: float) -> float | None:
    if not xs:
        return None
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(p * (len(xs) - 1))))]


def provenance() -> dict:
    try:
        commit = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, text=True).strip()
    except Exception:  # noqa: BLE001
        commit = "unknown"
    return {"commit": commit, "generated_at": datetime.now(timezone.utc).isoformat(),
            "python": platform.python_version(), "machine": platform.processor() or platform.machine()}


def write_result(name: str, payload: dict) -> Path:
    RESULTS.mkdir(parents=True, exist_ok=True)
    p = RESULTS / f"{name}.json"
    p.write_text(json.dumps({"provenance": provenance(), **payload}, indent=2))
    return p


def read_detail(path: Path) -> list[dict]:
    """Rows of a *_detail.jsonl file, keeping the last row per job. Runners append when a run is resumed after a
    crash, so a job can appear more than once; the last row is the one that finished."""
    if not path.exists():
        return []
    out: dict[tuple, dict] = {}
    for line in path.read_text().splitlines():
        if line.strip():
            r = json.loads(line)
            out[(r.get("persona"), r.get("job_id"), r.get("system"), r.get("level"))] = r
    return list(out.values())
