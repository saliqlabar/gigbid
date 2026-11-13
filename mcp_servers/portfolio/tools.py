"""Portfolio read tools. Every lookup is scoped to the caller's persona (tenant)."""
from __future__ import annotations

import os
from collections import defaultdict
from typing import Literal

from pydantic import BaseModel, Field

from rag.retriever import get_corpus, get_retriever
from rag.variants.base import Query

EvidenceType = Literal["cv", "case", "readme", "code", "review", "proposal"]


class Filters(BaseModel):
    types: list[EvidenceType] | None = Field(None, description="Only these evidence types")
    project: str | None = Field(None, max_length=40, description="Only evidence from this project id, e.g. p03")
    since: str | None = Field(None, max_length=10, description="Only evidence dated on/after YYYY-MM-DD")
    outcome: Literal["won", "replied", "ignored"] | None = Field(None, description="Proposals with this outcome")
    client_industry: str | None = Field(None, max_length=60, description="Only work for clients in this industry, e.g. healthcare")


class Evidence(BaseModel):
    evidence_id: str
    type: str
    title: str
    source: str
    date: str | None = None
    outcome: str | None = None
    project: str | None = None
    client_industry: str | None = None
    skills: list[str] = []
    score: float
    snippet: str
    uri: str


class EvidenceList(BaseModel):
    requirement: str
    variant: str
    results: list[Evidence]


class CaseStudy(BaseModel):
    evidence_id: str
    title: str
    project: str
    client_industry: str | None
    date: str | None
    skills: list[str]
    text: str
    related: list[str] = Field(description="Other evidence ids for the same project (README, code, review)")


class Skill(BaseModel):
    skill: str
    projects: int
    evidence_ids: list[str]


class SkillList(BaseModel):
    persona: str
    skills: list[Skill]


def persona_of(freelancer_id: str) -> str:
    return os.getenv("PORTFOLIO_PERSONA") or freelancer_id


def uri(evidence_id: str) -> str:
    _, typ, rest = evidence_id.split(":", 2) if evidence_id.count(":") >= 2 else (None, "cv", "")
    return f"portfolio://{typ}/{rest}" if rest else "portfolio://cv"


def _ok(it, f: Filters | None) -> bool:
    if not f:
        return True
    return ((not f.types or it.type in f.types) and (not f.project or it.project == f.project)
            and (not f.since or (it.date or "") >= f.since) and (not f.outcome or it.outcome == f.outcome)
            and (not f.client_industry or (it.client_industry or "").lower() == f.client_industry.lower()))


def search_evidence(persona: str, requirement: str, k: int, filters: Filters | None) -> EvidenceList:
    corpus = get_corpus(persona)
    pipe = get_retriever()
    wants_proposals = bool(filters and filters.types and "proposal" in filters.types)
    if wants_proposals:
        # proposals are not in the fit-evidence index; search them with the same pipeline on their own corpus
        from rag.retriever import PersonaCorpus
        corpus = PersonaCorpus(persona, corpus.all_items, types=("proposal",))
    hits = pipe.retrieve(Query(text=requirement, persona=persona, requirements=[requirement]), corpus, k=max(k * 4, 20))
    out = []
    for h in hits:
        if _ok(h.item, filters):
            it = h.item
            out.append(Evidence(evidence_id=it.evidence_id, type=it.type, title=it.title, source=it.source,
                                date=it.date, outcome=it.outcome, project=it.project,
                                client_industry=it.client_industry, skills=it.skills, score=round(h.score, 4),
                                snippet=(h.snippet or it.text)[:600], uri=uri(it.evidence_id)))
        if len(out) >= k:
            break
    return EvidenceList(requirement=requirement, variant=pipe.name, results=out)


def get_item(persona: str, evidence_id: str):
    it = get_corpus(persona).by_id.get(evidence_id)
    if it is None or it.persona != persona:
        raise KeyError(f"no evidence {evidence_id!r} in this portfolio")
    return it


def get_case_study(persona: str, case_id: str) -> CaseStudy:
    eid = case_id if ":" in case_id else f"{persona}:case:{case_id}"
    it = get_item(persona, eid)
    if it.type != "case":
        raise KeyError(f"{eid} is a {it.type}, not a case study")
    related = sorted(e for e, x in get_corpus(persona).by_id.items() if x.project == it.project and e != eid
                     and x.type != "proposal")
    return CaseStudy(evidence_id=eid, title=it.title, project=it.project, client_industry=it.client_industry,
                     date=it.date, skills=it.skills, text=it.text, related=related)


def list_skills(persona: str) -> SkillList:
    acc: dict[str, set[str]] = defaultdict(set)
    projects: dict[str, set[str]] = defaultdict(set)
    for it in get_corpus(persona).items:
        for s in it.skills:
            acc[s].add(it.evidence_id)
            if it.project:
                projects[s].add(it.project)
    skills = [Skill(skill=s, projects=len(projects[s]), evidence_ids=sorted(acc[s])) for s in acc]
    return SkillList(persona=persona, skills=sorted(skills, key=lambda x: (-x.projects, x.skill)))
