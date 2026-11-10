"""Shared types and the composable retrieval pipeline.

Each variant file (v0_naive.py ... v8_corrective.py) implements ONE component and a preset of flags.
`Pipeline` composes components from flags, so the ablation is "best preset minus one flag" and one
command reruns the whole comparison (evals/runners/rag_eval.py).
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

from rag.ingest.loaders import EvidenceItem

FIT_TYPES = ("cv", "case", "readme", "code", "review")


@dataclass
class Query:
    text: str                                   # the full job post (title + description)
    persona: str
    requirements: list[str] = field(default_factory=list)
    category: str | None = None


@dataclass
class Hit:
    evidence_id: str
    score: float
    item: EvidenceItem
    snippet: str = ""
    requirement: str | None = None
    grade: str | None = None                    # V8: relevant | weak | none


@dataclass(frozen=True)
class Flags:
    chunking: str = "fixed"         # fixed | section
    decompose: bool = False         # V1
    hybrid: bool = False            # V2
    rerank: bool = False            # V3
    outcome_boost: bool = False     # V4
    multivector: bool = False       # V5
    corrective: bool = False        # V8
    top_chunks: int = 40
    rerank_top: int = 20

    def without(self, name: str) -> "Flags":
        default = Flags()
        return replace(self, **{name: getattr(default, name)})

    def as_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


def rrf(rankings: list[list[str]], k: int = 60) -> dict[str, float]:
    """Reciprocal rank fusion over ranked id lists."""
    out: dict[str, float] = {}
    for ranking in rankings:
        for r, key in enumerate(ranking):
            out[key] = out.get(key, 0.0) + 1.0 / (k + r + 1)
    return out
