"""Chunkers. V0 uses fixed-size character windows; other variants use structure-aware units."""
from __future__ import annotations

import re
from dataclasses import dataclass

from rag.ingest.loaders import EvidenceItem


@dataclass
class Chunk:
    chunk_id: str
    evidence_id: str
    text: str
    kind: str = "fixed"   # fixed | section | summary | readme | code | context


def fixed_chunks(item: EvidenceItem, size: int = 600, overlap: int = 100) -> list[Chunk]:
    t = item.text
    if len(t) <= size:
        return [Chunk(f"{item.evidence_id}#0", item.evidence_id, t)]
    out, i, n = [], 0, 0
    while i < len(t):
        out.append(Chunk(f"{item.evidence_id}#{n}", item.evidence_id, t[i:i + size]))
        i += size - overlap
        n += 1
    return out


def section_chunks(item: EvidenceItem, max_chars: int = 1200) -> list[Chunk]:
    """Split Markdown on headings and code on `# file:` markers, keeping the title on every chunk."""
    parts = re.split(r"\n(?=#{1,3} |# file: )", item.text)
    out = []
    for n, p in enumerate(x for x in parts if x.strip()):
        body = p if p.startswith(item.title) else f"{item.title}\n{p}"
        out.append(Chunk(f"{item.evidence_id}#s{n}", item.evidence_id, body[:max_chars], kind="section"))
    return out or [Chunk(f"{item.evidence_id}#s0", item.evidence_id, item.text[:max_chars], "section")]
