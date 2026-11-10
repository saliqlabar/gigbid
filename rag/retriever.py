"""Retriever factory: the variant that ships is chosen in agent/config.yaml (`rag.variant`), by evaluation.

    corpus = get_corpus("fullstack")
    hits = get_retriever().retrieve(Query(text=job_text, persona="fullstack", requirements=[...]), corpus, k=5)
"""
from __future__ import annotations

import threading
from functools import lru_cache

from agent.settings import load_config
from rag.index.bm25 import BM25Index
from rag.index.vector import VectorIndex
from rag.ingest.chunkers import fixed_chunks, section_chunks
from rag.ingest.loaders import EvidenceItem, load_persona
from rag.variants import v0_naive, v1_decompose, v2_hybrid, v3_rerank, v4_outcome, v5_multivector, v8_corrective
from rag.variants.base import FIT_TYPES, Flags, Hit, Query, rrf

PRESETS: dict[str, Flags] = {
    "v0_naive": v0_naive.PRESET,
    "v1_decompose": v1_decompose.PRESET,
    "v2_hybrid": v2_hybrid.PRESET,
    "v3_rerank": v3_rerank.PRESET,
    "v4_outcome": v4_outcome.PRESET,
    "v5_multivector": v5_multivector.PRESET,
    "v8_corrective": v8_corrective.PRESET,
}


class PersonaCorpus:
    """All evidence for one persona (tenant), with lazily built indexes per chunking scheme."""

    def __init__(self, persona: str, items: list[EvidenceItem], types: tuple[str, ...] = FIT_TYPES):
        self.persona = persona
        self.all_items = items
        self.items = [i for i in items if i.type in types]
        self.by_id = {i.evidence_id: i for i in items}
        self._vec: dict[str, VectorIndex] = {}
        self._bm25: dict[str, BM25Index] = {}
        self._lock = threading.Lock()
        self.outcome_prior = v4_outcome.project_outcome_prior(items)

    def chunks(self, chunking: str):
        fn = fixed_chunks if chunking == "fixed" else section_chunks
        return [c for it in self.items for c in fn(it)]

    def vector(self, chunking: str) -> VectorIndex:
        with self._lock:
            if chunking not in self._vec:
                self._vec[chunking] = VectorIndex(self.chunks(chunking))
            return self._vec[chunking]

    def bm25(self, chunking: str) -> BM25Index:
        with self._lock:
            if chunking not in self._bm25:
                self._bm25[chunking] = BM25Index(self.chunks(chunking))
            return self._bm25[chunking]


_corpora: dict[str, PersonaCorpus] = {}


def get_corpus(persona: str, refresh: bool = False) -> PersonaCorpus:
    """Incremental re-index: `refresh=True` reloads the persona; unchanged chunks hit the embedding cache."""
    if refresh or persona not in _corpora:
        _corpora[persona] = PersonaCorpus(persona, load_persona(persona))
    return _corpora[persona]


class Pipeline:
    def __init__(self, flags: Flags, name: str = "custom"):
        self.flags, self.name = flags, name

    def retrieve_per_query(self, q: Query, corpus: PersonaCorpus) -> list[tuple[str, list[Hit]]]:
        """[(query_text, ranked hits)] - one entry per (decomposed) query."""
        f = self.flags
        out = []
        for qt in v1_decompose.queries(q, f):
            cands = v0_naive.dense_chunk_scores(corpus, qt, f.chunking, f.top_chunks)
            if f.hybrid:
                cands = v2_hybrid.hybrid_rank(corpus, qt, f.chunking, f.top_chunks, cands)
            if f.rerank:
                cands = v3_rerank.rerank_scores(qt, cands, f.rerank_top)
            if f.outcome_boost:
                cands = {e: (v4_outcome.boost(s, corpus.outcome_prior.get(corpus.by_id[e].project or "")), t)
                         for e, (s, t) in cands.items()}
            ranked = sorted(cands, key=lambda e: -cands[e][0])
            out.append((qt, [Hit(e, cands[e][0], corpus.by_id[e], cands[e][1][:500], qt) for e in ranked]))
        return out

    def retrieve(self, q: Query, corpus: PersonaCorpus, k: int = 5) -> list[Hit]:
        if self.flags.corrective:
            reqs = v1_decompose.queries(q, self.flags)
            per_query = v8_corrective.correct(self, q, corpus, [(r, []) for r in reqs])
        else:
            per_query = self.retrieve_per_query(q, corpus)
        if len(per_query) == 1:
            hits = per_query[0][1]
        else:
            fused = rrf([[h.evidence_id for h in hs] for _, hs in per_query])
            first = {}
            for _, hs in per_query:
                for h in hs:
                    if h.evidence_id not in first or h.score > first[h.evidence_id].score:
                        first[h.evidence_id] = h
            hits = [Hit(e, s, first[e].item, first[e].snippet, first[e].requirement, first[e].grade)
                    for e, s in sorted(fused.items(), key=lambda x: -x[1])]
        if self.flags.multivector:
            by = {h.evidence_id: h for h in hits}
            order = v5_multivector.group_by_project([h.evidence_id for h in hits], corpus.by_id)
            hits = [by.get(e) or Hit(e, 0.0, corpus.by_id[e], corpus.by_id[e].text[:500], None) for e in order]
        return hits[:k]


@lru_cache(maxsize=8)
def get_retriever(variant: str | None = None) -> Pipeline:
    import os
    variant = variant or os.getenv("RAG_VARIANT") or load_config().get("rag", {}).get("variant", "v0_naive")
    return Pipeline(PRESETS[variant], variant)
