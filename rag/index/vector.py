"""Dense vector index over chunks (exact cosine). The portfolio is a few thousand chunks per persona, so
exact search in NumPy is both the simplest and the most accurate choice; see docs/adr/0003-vector-store.md.
"""
from __future__ import annotations

import numpy as np

from rag.index.embedder import embed
from rag.ingest.chunkers import Chunk


class VectorIndex:
    def __init__(self, chunks: list[Chunk]):
        self.chunks = chunks
        self.matrix = embed([c.text for c in chunks], kind="doc")

    def search(self, query: str | np.ndarray, top: int = 50) -> list[tuple[Chunk, float]]:
        q = embed([query], kind="query")[0] if isinstance(query, str) else query
        if not self.chunks:
            return []
        sims = self.matrix @ q
        idx = np.argsort(-sims)[:top]
        return [(self.chunks[i], float(sims[i])) for i in idx]
