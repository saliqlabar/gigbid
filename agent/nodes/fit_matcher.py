"""Fit Matcher: every requirement -> portfolio evidence (via the Portfolio MCP server) -> fit score.

Runs in PARALLEL with the Job Analyst, so it cannot wait for the JobSpec. It takes requirements from the
post's own structured skill tags (plus the title), retrieves evidence per requirement, then one small-
model call grades each requirement strong / partial / none against the retrieved text.
Score = mean(strong=1, partial=0.5, none=0). Evidence found here is what the Writer may cite.
"""
from __future__ import annotations

import asyncio

from pydantic import BaseModel

from agent.handoffs import FitReport, RequirementMatch
from agent.llm import structured
from agent.mcp_client import ToolLayer
from agent.nodes._base import node
from agent.settings import load_config

GENERIC = {"python", "javascript", "web development", "data analysis", "machine learning", "api",
           "artificial intelligence", "data entry", "english", "microsoft excel"}


class _Grades(BaseModel):
    matches: list[RequirementMatch]


def requirements_from_job(job: dict, limit: int = 6) -> list[str]:
    tags = [t for t in job.get("skills", []) if t]
    specific = [t for t in tags if t.lower() not in GENERIC]
    reqs = (specific + [t for t in tags if t.lower() in GENERIC])[:limit]
    return reqs or [job["title"]]


async def match(job: dict, tools: ToolLayer, profile: dict) -> tuple[FitReport, list[dict]]:
    k = int(load_config().get("fit", {}).get("evidence_per_requirement", 3))
    reqs = requirements_from_job(job)
    results = await asyncio.gather(*[tools.call("fit_matcher", "portfolio.search_evidence",
                                                {"requirement": r, "k": k}) for r in reqs])
    evidence: dict[str, dict] = {}
    retrieved: dict[str, list[str]] = {}
    blocks = []
    for r, res in zip(reqs, results):
        lines = []
        retrieved[r] = [e["evidence_id"] for e in res.get("results", [])]
        for e in res.get("results", []):
            evidence.setdefault(e["evidence_id"], {**e, "requirements": []})["requirements"].append(r)
            lines.append(f"  [{e['evidence_id']}] ({e['type']}) {e['snippet'][:350]}")
        blocks.append(f"REQUIREMENT: {r}\n" + ("\n".join(lines) or "  (no evidence found)"))
    grades = await structured("fit_matcher", "fit_matcher",
                              {"headline": profile.get("headline", ""), "requirements_with_evidence": "\n\n".join(blocks)},
                              _Grades)
    by_req = {m.requirement.strip().lower(): m for m in grades.matches}
    matches = []
    for r in reqs:
        m = by_req.get(r.lower()) or RequirementMatch(requirement=r, strength="none", reason="not graded")
        m.requirement = r
        m.evidence_ids = [e for e in m.evidence_ids if e in evidence]   # no ids the retriever did not return
        if m.strength != "none" and not m.evidence_ids:
            m.evidence_ids = retrieved.get(r, [])[:2]     # grader forgot to cite: use what was retrieved for it
            if not m.evidence_ids:
                m.strength, m.reason = "none", "no evidence retrieved"
        matches.append(m)
    val = {"strong": 1.0, "partial": 0.5, "none": 0.0}
    score = round(sum(val[m.strength] for m in matches) / len(matches), 3)
    used = sorted({e for m in matches for e in m.evidence_ids})
    rep = FitReport(score=score, matches=matches,
                    matched_skills=[m.requirement for m in matches if m.strength == "strong"],
                    missing_skills=[m.requirement for m in matches if m.strength == "none"],
                    evidence_ids=used,
                    reasons=[f"{m.requirement}: {m.strength} - {m.reason}" for m in matches])
    return rep, [evidence[e] for e in used]


@node("fit_matcher")
async def fit_matcher(state: dict, tools: ToolLayer) -> dict:
    rep, ev = await match(state["job"], tools, state.get("profile") or {})
    return {"fit_report": rep.model_dump(), "evidence": ev, "analyses": {"fit": "ok"}}
