"""V8 corrective RAG loop, as a small LangGraph StateGraph.

    retrieve -> grade --(all requirements have relevant evidence)--> done
                  \--(some weak/none, rewrites left)--> rewrite -> retrieve (only those requirements)
                  \--(no rewrites left)--> done, with ungrounded requirements returning NO evidence

Grading uses the cross-encoder score (cheap, deterministic, no LLM). Rewriting asks the small model to
restate the requirement the way a portfolio would describe having done it (skills are often implied,
not named). A requirement with no relevant evidence after the rewrite returns nothing, so the writer
cannot claim it ("refuse to claim when none").
"""
from __future__ import annotations

from typing import Any, TypedDict

from rag.index.embedder import rerank
from rag.variants.base import Flags, Hit, Query

PRESET = Flags(decompose=True, hybrid=True, rerank=True, multivector=True, corrective=True)
RELEVANT, WEAK = 0.0, -3.0   # bge-reranker-v2-m3 logits; overridable in config rag.corrective


class CState(TypedDict, total=False):
    pending: list[str]                       # requirements to (re)retrieve
    results: dict[str, list[Hit]]            # requirement -> graded hits
    rewrites: dict[str, str]                 # requirement -> current query text
    rounds: int


def _thresholds() -> tuple[float, float, int]:
    from agent.settings import load_config
    c = load_config().get("rag", {}).get("corrective", {})
    return float(c.get("relevant", RELEVANT)), float(c.get("weak", WEAK)), int(c.get("max_rewrites", 1))


def _grade(req: str, hits: list[Hit]) -> list[Hit]:
    rel, weak, _ = _thresholds()
    if not hits:
        return []
    # always grade against the ORIGINAL requirement, even when retrieval used a rewrite
    scores = rerank(req, [h.snippet for h in hits[:10]])
    for h, s in zip(hits, scores):
        h.grade = "relevant" if s >= rel else "weak" if s >= weak else "none"
    return hits[:10]


def build_graph(pipe, q: Query, corpus):
    from langgraph.graph import END, StateGraph

    from rag.variants.base import Flags as F

    inner = pipe.__class__(F(**{**pipe.flags.as_dict(), "corrective": False, "decompose": False}))

    def retrieve(s: CState) -> CState:
        res = dict(s.get("results", {}))
        for req in s["pending"]:
            qt = s.get("rewrites", {}).get(req, req)
            _, hits = inner.retrieve_per_query(Query(text=qt, persona=q.persona), corpus)[0]
            res[req] = _grade(req, hits)
        return {"results": res}

    def route(s: CState) -> str:
        _, _, max_rw = _thresholds()
        weak = [r for r, hs in s["results"].items() if not any(h.grade == "relevant" for h in hs)]
        return "rewrite" if weak and s.get("rounds", 0) < max_rw else END

    def rewrite(s: CState) -> CState:
        from agent.llm import complete_text
        weak = [r for r, hs in s["results"].items() if not any(h.grade == "relevant" for h in hs)]
        rw = dict(s.get("rewrites", {}))
        for r in weak:
            rw[r] = complete_text("rag_rewrite", {"requirement": r, "job": q.text[:1500]}).strip() or r
        return {"pending": weak, "rewrites": rw, "rounds": s.get("rounds", 0) + 1}

    g = StateGraph(CState)
    g.add_node("retrieve", retrieve)
    g.add_node("rewrite", rewrite)
    g.set_entry_point("retrieve")
    g.add_conditional_edges("retrieve", route, {"rewrite": "rewrite", END: END})
    g.add_edge("rewrite", "retrieve")
    return g.compile()


def correct(pipe, q: Query, corpus, per_query: list[tuple[str, list[Hit]]]) -> list[tuple[str, list[Hit]]]:
    reqs = [qt for qt, _ in per_query]
    state: dict[str, Any] = build_graph(pipe, q, corpus).invoke({"pending": reqs, "results": {}, "rounds": 0})
    out = []
    for r in reqs:
        hs = [h for h in state["results"].get(r, []) if h.grade == "relevant"]
        for h in hs:
            h.requirement = r
        out.append((r, hs))       # empty list = no evidence: the writer must not claim this requirement
    return out
