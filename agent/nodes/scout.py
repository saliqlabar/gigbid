"""Scout: runs saved searches, de-duplicates and queues new jobs; also the graph's intake node.

`scout_saved_search` is called by the worker on each saved search's schedule. `intake` is the first
node of the bid graph: it loads the full job, the client history and the current platform policy.
Near-duplicates (the same post re-listed with small edits) are caught with word-shingle Jaccard.
"""
from __future__ import annotations

import re
from typing import Any

from agent.mcp_client import ToolLayer
from agent.nodes._base import node
from agent.safety.injection import detect

_W = re.compile(r"[a-z0-9]+")


def shingles(text: str, n: int = 5) -> set[str]:
    w = _W.findall((text or "").lower())
    return {" ".join(w[i:i + n]) for i in range(max(1, len(w) - n + 1))}


def jaccard(a: set[str], b: set[str]) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def find_duplicate(job: dict, seen: list[dict], threshold: float = 0.8) -> str | None:
    """job_id of an already-seen job this one near-duplicates, else None."""
    sig = shingles(job["title"] + " " + job.get("description", ""))
    for s in seen:
        if s["job_id"] != job["job_id"] and jaccard(sig, s["sig"]) >= threshold:
            return s["job_id"]
    return None


async def scout_saved_search(tools: ToolLayer, search: dict, seen: list[dict], since: str = "") -> list[dict]:
    """Run one saved search; return new, non-duplicate jobs (each with `duplicate_of` set or None)."""
    res = await tools.call("scout", "marketplace.search_jobs",
                           {"query": search.get("query", ""), "category": search.get("category", ""),
                            "since": since, "limit": int(search.get("limit", 20))})
    out = []
    known = {s["job_id"] for s in seen}
    for j in res.get("jobs", []):
        if j["job_id"] in known:
            continue
        j["duplicate_of"] = find_duplicate(j, seen)
        seen.append({"job_id": j["job_id"], "sig": shingles(j["title"] + " " + j.get("description", ""))})
        known.add(j["job_id"])
        out.append(j)
    return out


@node("intake")
async def intake(state: dict, tools: ToolLayer) -> dict[str, Any]:
    job = state.get("job") or {}
    if "description" not in job:
        job = await tools.call("scout", "marketplace.get_job", {"job_id": job["job_id"]})
    policy = state.get("policy") or await tools.call("scout", "marketplace.get_policy", {})
    return {"job": job, "policy": policy, "status": "analysing",
            "analyses": {"injection_signatures": detect(job.get("title", "") + "\n" + job.get("description", ""))}}
