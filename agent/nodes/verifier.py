"""Claim verification node: runs rag/verify.py on the draft before the Critic sees it (B3)."""
from __future__ import annotations

from agent.mcp_client import ToolLayer
from agent.nodes._base import node
from agent.nodes.writer import evidence_pack
from rag.verify import citations, verify_many


async def evidence_texts(state: dict, tools: ToolLayer, text: str) -> dict[str, str]:
    pack = evidence_pack(state)
    out = {eid: e["snippet"] for eid, e in pack.items()}
    for eid in set(citations(text)) & set(pack):
        full = await tools.call("verifier", "portfolio.get_evidence", {"evidence_id": eid})
        out[eid] = full.get("snippet", out[eid])
    return out


@node("verifier")
async def verifier(state: dict, tools: ToolLayer) -> dict:
    d = dict(state["proposal_draft"])
    ev = await evidence_texts(state, tools, d["cover_letter"] + " " + " ".join(a["answer"] for a in d["screening_answers"]))
    res = await verify_many([d["cover_letter"]] + [a["answer"] for a in d["screening_answers"]], ev)
    v = res[0]
    answers = []
    for a, va in zip(d["screening_answers"], res[1:]):
        v.claims += va.claims
        v.unsupported_before += va.unsupported_before
        answers.append({**a, "answer": va.revised_text or "I have not done this exact thing before; I would start "
                                                          "by confirming the requirements with you."})
    d["cover_letter"], d["screening_answers"] = v.revised_text, answers
    return {"verification": v.model_dump(), "proposal_draft": d,
            "drafts": [{"stage": "verified", **d}]}
