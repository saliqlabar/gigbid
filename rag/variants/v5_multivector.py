"""V5 multi-vector items: case study, README and code are indexed separately but returned as ONE project.

A repo whose README says "Internal tool. See code." is invisible on its own; if its code chunk or the
case study matches, the whole project (all three views) comes back together.
"""
from rag.variants.base import Flags

PRESET = Flags(decompose=True, hybrid=True, rerank=True, multivector=True)
PROJECT_TYPES = ("case", "readme", "code")


def group_by_project(ranked: list[str], items_by_id: dict) -> list[str]:
    """Expand each hit to all views of its project, keeping the order of first appearance."""
    by_project: dict[str, list[str]] = {}
    for eid, it in items_by_id.items():
        if it.project and it.type in PROJECT_TYPES:
            by_project.setdefault(it.project, []).append(eid)
    out, seen = [], set()
    for eid in ranked:
        it = items_by_id[eid]
        group = by_project.get(it.project, [eid]) if it.type in PROJECT_TYPES else [eid]
        # the matched view first, then the other views of the same project
        for g in [eid] + sorted(x for x in group if x != eid):
            if g not in seen:
                seen.add(g)
                out.append(g)
    return out
