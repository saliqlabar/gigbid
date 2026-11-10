"""V0 naive dense baseline: fixed 600-char chunks, embed the WHOLE job post, top-k cosine."""
from rag.variants.base import Flags

PRESET = Flags()


def dense_chunk_scores(corpus, query_text: str, chunking: str, top: int) -> dict[str, tuple[float, str]]:
    """Best chunk per evidence item: {evidence_id: (cosine, chunk_text)}."""
    best: dict[str, tuple[float, str]] = {}
    for ch, s in corpus.vector(chunking).search(query_text, top=top):
        if ch.evidence_id not in best or s > best[ch.evidence_id][0]:
            best[ch.evidence_id] = (s, ch.text)
    return best


def retrieve(query: str, index, k: int = 5):
    """Kept for the starter-kit signature: `index` is a rag.retriever.PersonaCorpus."""
    from rag.retriever import Pipeline
    from rag.variants.base import Query
    return Pipeline(PRESET).retrieve(Query(text=query, persona=index.persona), index, k)
