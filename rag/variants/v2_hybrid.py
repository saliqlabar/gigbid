"""V2 hybrid retrieval: BM25 + dense, fused with reciprocal rank fusion.

Exact tool names ("Celery", "dbt", "Pinecone") are where embeddings blur and BM25 is sharp.
"""
from rag.variants.base import Flags, rrf

PRESET = Flags(decompose=True, hybrid=True)


def hybrid_rank(corpus, query_text: str, chunking: str, top: int, dense: dict[str, tuple[float, str]]
                ) -> dict[str, tuple[float, str]]:
    sparse: dict[str, tuple[float, str]] = {}
    for ch, s in corpus.bm25(chunking).search(query_text, top=top):
        if ch.evidence_id not in sparse or s > sparse[ch.evidence_id][0]:
            sparse[ch.evidence_id] = (s, ch.text)
    d_rank = sorted(dense, key=lambda e: -dense[e][0])
    s_rank = sorted(sparse, key=lambda e: -sparse[e][0])
    fused = rrf([d_rank, s_rank])
    return {e: (sc, (dense.get(e) or sparse[e])[1]) for e, sc in fused.items()}
