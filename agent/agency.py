"""X1 Agency mode: a team of freelancers; each job is routed to the best-fit member.

Routing reuses the production Fit Matcher (`agent/nodes/fit_matcher.match`) once per member, each against that
member's own portfolio through the Portfolio MCP server (the ToolLayer's freelancer id selects the persona).
The member with the highest fit score wins; ties go to the member with more `strong` requirements, then to the
higher sum of retrieval scores. If nobody reaches `agency.min_fit_to_route`, the job is routed to no one.
The chosen member's profile then drives the Writer's voice (`writer.voice_line`) and their evidence is cited.
"""
from __future__ import annotations

import asyncio

from pydantic import BaseModel, Field

from agent.mcp_client import ToolLayer
from agent.nodes.fit_matcher import match
from agent.runner import profile_for
from agent.settings import load_config


class MemberFit(BaseModel):
    member: str
    fit: float
    strong: int
    matched: list[str] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)


class AgencyRoute(BaseModel):
    member: str | None                 # None = nobody on the team fits
    ranking: list[MemberFit]
    reason: str


def members() -> list[str]:
    return list(load_config().get("agency", {}).get("members", []))


async def route_job(job: dict, user_id: str, trace_id: str, team: list[str] | None = None) -> AgencyRoute:
    team = team or members()

    async def one(m: str) -> MemberFit:
        rep, _ = await match(job, ToolLayer(user_id, m, trace_id), profile_for(m))
        return MemberFit(member=m, fit=rep.score, strong=len(rep.matched_skills), matched=rep.matched_skills,
                         missing=rep.missing_skills)
    ranking = sorted(await asyncio.gather(*[one(m) for m in team]), key=lambda f: (f.fit, f.strong), reverse=True)
    floor = float(load_config().get("agency", {}).get("min_fit_to_route", 0.2))
    best = ranking[0]
    if best.fit < floor:
        return AgencyRoute(member=None, ranking=ranking, reason=f"no member reaches fit {floor} (best {best.member} {best.fit})")
    runner_up = ranking[1] if len(ranking) > 1 else None
    why = f"{best.member} fit {best.fit} ({', '.join(best.matched[:3]) or 'partial matches'})"
    if runner_up:
        why += f" vs {runner_up.member} {runner_up.fit}"
    return AgencyRoute(member=best.member, ranking=ranking, reason=why)
