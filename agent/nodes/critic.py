"""Critic: grounding, personalisation and length; sends the draft back to the Writer at most
`budgets.max_revisions` times (2). Deterministic checks (length, generic opening) back up the model."""
from __future__ import annotations

from agent.handoffs import Critique
from agent.llm import structured
from agent.mcp_client import ToolLayer
from agent.nodes._base import node
from agent.settings import load_config

GENERIC_OPENINGS = ("dear", "hello", "hi ", "hi,", "greetings", "i am very interested", "i have read your",
                    "to whom it may concern", "i am excited", "i'm excited", "i would love to")


def deterministic(text: str, max_words: int) -> tuple[bool, bool]:
    first = text.strip().lower()[:80]
    return len(text.split()) <= max_words, any(first.startswith(g) for g in GENERIC_OPENINGS)


@node("critic")
async def critic(state: dict, tools: ToolLayer) -> dict:
    d = state["proposal_draft"]
    max_words = (state.get("policy") or {}).get("rules", {}).get("max_cover_letter_words", 250)
    v = state.get("verification") or {}
    claims = [f"{c['status']}: {c['claim'][:80]}" for c in v.get("claims", []) if c["status"] != "not_a_claim"]
    c = await structured("critic", "critic", {
        "max_words": max_words, "job_title": state["job"]["title"], "words": len(d["cover_letter"].split()),
        "client_specifics": "; ".join((state.get("job_spec") or {}).get("client_specifics", [])),
        "draft": d["cover_letter"], "claims": "; ".join(claims) or "none"}, Critique)
    length_ok, generic = deterministic(d["cover_letter"], max_words)
    c.length_ok = c.length_ok and length_ok
    c.generic_opening = c.generic_opening or generic
    if not length_ok:
        c.issues.append(f"over {max_words} words")
    if generic:
        c.issues.append("generic opening line")
    if v.get("unsupported_before"):
        c.issues.append(f"{v['unsupported_before']} unsupported claim(s) were removed; keep claims within evidence")
    c.approve = c.approve and c.length_ok and not c.generic_opening and c.grounding >= 4
    revisions = int(state.get("revisions", 0))
    out = {"critiques": [{**c.model_dump(), "revision": revisions}]}
    if not c.approve and revisions < int(load_config()["budgets"]["max_revisions"]):
        out["revisions"] = revisions + 1
    return out


def route_after_critic(state: dict) -> str:
    from agent.nodes._base import halted
    if halted(state):
        return "end"
    last = state["critiques"][-1]
    if not last["approve"] and last["revision"] < int(load_config()["budgets"]["max_revisions"]):
        return "writer"
    return "compliance"
