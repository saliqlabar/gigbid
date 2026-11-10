"""V3 cross-encoder reranking (bge-reranker-v2-m3) of the top candidates per query."""
from rag.index.embedder import rerank
from rag.variants.base import Flags

PRESET = Flags(decompose=True, hybrid=True, rerank=True)


def rerank_scores(query_text: str, cands: dict[str, tuple[float, str]], top: int) -> dict[str, tuple[float, str]]:
    head = sorted(cands, key=lambda e: -cands[e][0])[:top]
    scores = rerank(query_text, [cands[e][1] for e in head])
    return {e: (s, cands[e][1]) for e, s in zip(head, scores)}
