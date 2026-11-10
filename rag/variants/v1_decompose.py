"""V1 requirement decomposition: one retrieval per extracted requirement, merged with RRF.

Requirements come from the Job Analyst's JobSpec (deliverables + required skills); a job asking for
five things gets five focused queries instead of one blurred embedding of the whole post.
"""
from rag.variants.base import Flags, Query

PRESET = Flags(decompose=True)


def queries(q: Query, flags: Flags) -> list[str]:
    if flags.decompose and q.requirements:
        return [f"{r}" for r in q.requirements]
    return [q.text]
