"""Claim verification (B3): split a draft into sentence-level claims and check each against evidence.

Each sentence is labelled supported / partly_supported / unsupported / not_a_claim by the small model,
judging against the TEXT of the evidence (cited items are fetched in full), not against the citation.
Unsupported sentences are removed before the Critic sees the draft; partly supported ones are kept and
flagged for the Critic and the human. Citations to ids outside the evidence pack are stripped.
"""
from __future__ import annotations

import re

from pydantic import BaseModel, Field

from agent.handoffs import ClaimCheck, Verification

CITE = re.compile(r"\s*\[([a-z_]+:[a-z]+(?::[a-z0-9]+)?)\]")


class _Out(BaseModel):
    class C(BaseModel):
        n: int
        status: str
        evidence_ids: list[str] = Field(default_factory=list)
        note: str = ""
    claims: list[C]


def split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z\"'(])|\n+", text.strip())
    return [p.strip() for p in parts if p and p.strip()]


def citations(text: str) -> list[str]:
    return CITE.findall(text)


async def verify_many(texts: list[str], evidence: dict[str, str]) -> list[Verification]:
    """Verify several texts (cover letter + screening answers) in ONE model call."""
    from agent.llm import structured
    sents = [split_sentences(t) for t in texts]
    flat = [(ti, s) for ti, ss in enumerate(sents) for s in ss]
    if not flat:
        return [Verification(revised_text=t) for t in texts]
    ev_block = "\n\n".join(f"[{eid}]\n{t[:1200]}" for eid, t in evidence.items()) or "(no evidence)"
    numbered = "\n".join(f"{i + 1}. {CITE.sub('', s)}" for i, (_, s) in enumerate(flat))
    out = await structured("claim_verifier", "claim_verifier", {"evidence": ev_block, "sentences": numbered}, _Out)
    by_n = {c.n: c for c in out.claims}
    results = [Verification() for _ in texts]
    kept: list[list[str]] = [[] for _ in texts]
    for i, (ti, s) in enumerate(flat):
        c = by_n.get(i + 1)
        status = c.status if c and c.status in ("supported", "partly_supported", "unsupported", "not_a_claim") \
            else "not_a_claim"
        cited = [e for e in citations(s) if e in evidence]
        ids = [e for e in (c.evidence_ids if c else []) if e in evidence] or cited
        clean = CITE.sub(lambda m: m.group(0) if m.group(1) in evidence else "", s)
        if status in ("supported", "partly_supported") and not ids:
            # a claim cannot be (partly) supported by nothing: small verifier models say "partly" when they
            # find no evidence (seen on job_00411: "managed IP rotation ..."), so this rule is deterministic.
            status = "unsupported"
        if status == "unsupported":
            results[ti].claims.append(ClaimCheck(claim=s, status=status, evidence_ids=ids, note=c.note if c else "",
                                                 action="removed"))
            results[ti].unsupported_before += 1
            continue
        results[ti].claims.append(ClaimCheck(claim=clean, status=status, evidence_ids=ids, note=c.note if c else ""))
        kept[ti].append(clean)
    for ti, t in enumerate(texts):
        results[ti].revised_text = _join(t, kept[ti])
    return results


async def verify_text(text: str, evidence: dict[str, str]) -> Verification:
    return (await verify_many([text], evidence))[0]


def _join(original: str, sentences: list[str]) -> str:
    # keep paragraph breaks where the original had them
    paras, out = [p for p in original.split("\n") if p.strip()], []
    remaining = list(sentences)
    for p in paras:
        cur = []
        while remaining and remaining[0] in p:
            cur.append(remaining.pop(0))
        if cur:
            out.append(" ".join(cur))
    if remaining:
        out.append(" ".join(remaining))
    return "\n\n".join(out)


def verify_claims(draft: str, evidence: list[dict]) -> list[dict]:
    """Starter-kit signature (sync)."""
    import asyncio
    v = asyncio.run(verify_text(draft, {e["evidence_id"]: e.get("text") or e.get("snippet", "") for e in evidence}))
    return [c.model_dump() for c in v.claims]
