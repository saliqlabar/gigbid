"""Proposal Writer: cover letter + screening answers, citing evidence ids for every claim.
Reads promoted style rules and outcome lessons from long-term memory (never raw observations)."""
from __future__ import annotations

import asyncio
import json

from pydantic import BaseModel, Field

from agent.handoffs import ProposalDraft, ScreeningAnswer
from agent.llm import structured
from agent.mcp_client import ToolLayer
from agent.nodes._base import node


class _WriterOut(BaseModel):
    cover_letter: str = Field(description="the cover letter with [evidence_id] citations")
    screening_answers: list[ScreeningAnswer] = Field(default_factory=list)


def evidence_pack(state: dict) -> dict[str, dict]:
    """Only evidence the Fit Matcher graded strong/partial (plus screening-answer evidence) may be cited."""
    fit = state.get("fit_report") or {}
    allowed = {e for m in fit.get("matches", []) if m["strength"] != "none" for e in m["evidence_ids"]}
    pack = {}
    for e in state.get("evidence", []):
        if e["evidence_id"] in allowed or "screening" in e.get("requirements", []):
            pack[e["evidence_id"]] = e
    return pack


def format_evidence(pack: dict[str, dict]) -> str:
    return "\n".join(f"[{eid}] ({e['type']}, {e.get('date') or 'n/a'}) {e['title']}: {e['snippet'][:450]}"
                     for eid, e in pack.items()) or "(no evidence - do not claim any experience)"


async def screening_evidence(state: dict, tools: ToolLayer) -> list[dict]:
    qs = (state.get("job_spec") or {}).get("screening_questions", [])[:4]
    res = await asyncio.gather(*[tools.call("writer", "portfolio.search_evidence", {"requirement": q, "k": 2})
                                 for q in qs])
    out = []
    for r in res:
        for e in r.get("results", []):
            out.append({**e, "requirements": ["screening"]})
    return out


def voice_line(profile: dict) -> str:
    """Whose voice the letter is in (X1 agency mode routes each job to one team member)."""
    if not profile.get("headline"):
        return ""
    return (f"You are writing as {profile.get('display_name', 'the freelancer')}, {profile['headline']} "
            f"({profile.get('years', '?')} years). Write in their voice and claim only their evidence.")


async def write(state: dict, tools: ToolLayer, memory: dict) -> tuple[ProposalDraft, list[dict]]:
    extra = await screening_evidence(state, tools) if not state.get("proposal_draft") else []
    st = {**state, "evidence": state.get("evidence", []) + extra}
    pack = evidence_pack(st)
    spec = state.get("job_spec") or {}
    price = state.get("price_quote") or {}
    crit = (state.get("critiques") or [])[-1:] or [None]
    rules = memory.get("style_rules") or []
    lessons = memory.get("outcome") or {}
    revision_note = ""
    if crit[0] and not crit[0].get("approve"):
        prev = (state.get("proposal_draft") or {}).get("cover_letter", "")
        revision_note = (f"REVISION REQUESTED by the critic: {crit[0].get('revision_instructions')}\n"
                         f"Issues: {crit[0].get('issues')}\nPrevious draft:\n{prev}")
    out = await structured("writer", "writer", {
        "max_words": (state.get("policy") or {}).get("rules", {}).get("max_cover_letter_words", 250),
        "style_rules": ("The freelancer's learned style rules (follow them):\n- " + "\n- ".join(rules)) if rules else "",
        "outcome_lessons": f"What worked before in this category: {'; '.join(lessons.get('lessons', []))}"
        if lessons.get("lessons") else "",
        "job_spec": json.dumps({k: spec.get(k) for k in ("title", "summary", "deliverables", "required_skills",
                                                        "timeline", "hidden_needs")}),
        "client_specifics": "; ".join(spec.get("client_specifics", [])) or "(none extracted)",
        "price": f"{price.get('amount')} USD ({price.get('bid_type')})",
        "evidence": format_evidence(pack),
        "questions": "\n".join(f"- {q}" for q in spec.get("screening_questions", [])) or "(none)",
        "revision_note": revision_note,
        "voice": voice_line(state.get("profile") or {})}, _WriterOut, temperature=0.4)
    draft = ProposalDraft(job_id=state["job"]["job_id"], cover_letter=out.cover_letter,
                          screening_answers=out.screening_answers, revision=int(state.get("revisions", 0)))
    from rag.verify import citations
    cited = set(citations(draft.cover_letter)) | {c for a in draft.screening_answers for c in citations(a.answer)}
    draft.evidence_ids = sorted(e for e in cited if e in pack)
    return draft, extra


@node("writer")
async def writer(state: dict, tools: ToolLayer) -> dict:
    from agent.runtime import memory_for
    draft, extra = await write(state, tools, await memory_for(state))
    return {"proposal_draft": draft.model_dump(), "drafts": [{"stage": "writer", **draft.model_dump()}],
            "evidence": extra}
