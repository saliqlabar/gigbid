"""BM25 keyword index. Tokeniser keeps tool names like 'next.js', 'c#', 'power bi' intact-ish."""
from __future__ import annotations

import re

from rank_bm25 import BM25Okapi

from rag.ingest.chunkers import Chunk

_TOK = re.compile(r"[a-z0-9][a-z0-9+#.\-]*[a-z0-9+#]|[a-z0-9]")
STOP = set("the a an and or of to in for on with by is are was be as at it this that from i we you our your "
           "my me will can need looking someone who have has".split())


def tokenize(text: str) -> list[str]:
    return [t for t in _TOK.findall(text.lower()) if t not in STOP]


class BM25Index:
    def __init__(self, chunks: list[Chunk]):
        self.chunks = chunks
        self.bm25 = BM25Okapi([tokenize(c.text) for c in chunks]) if chunks else None

    def search(self, query: str, top: int = 50) -> list[tuple[Chunk, float]]:
        if not self.bm25:
            return []
        scores = self.bm25.get_scores(tokenize(query))
        order = sorted(range(len(scores)), key=lambda i: -scores[i])[:top]
        return [(self.chunks[i], float(scores[i])) for i in order if scores[i] > 0]
