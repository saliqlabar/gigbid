"""V4 outcome-aware ranking: boost evidence from projects that past WON / REPLIED proposals cited.

Past proposals carry outcomes. A project that has won work before is better evidence to lead with,
so its items get a multiplicative boost proportional to its win/reply record.
"""
from rag.variants.base import Flags

PRESET = Flags(decompose=True, hybrid=True, rerank=True, outcome_boost=True)
WEIGHT = {"won": 1.0, "replied": 0.5, "ignored": 0.0}


def project_outcome_prior(items) -> dict[str, float]:
    """Smoothed success score per project in [0, 1] from that persona's past proposals."""
    tot: dict[str, list[float]] = {}
    for it in items:
        if it.type == "proposal" and it.project:
            tot.setdefault(it.project, []).append(WEIGHT.get(it.outcome or "", 0.0))
    return {p: (sum(v) + 0.5) / (len(v) + 1.5) for p, v in tot.items()}


def boost(score: float, prior: float | None, strength: float = 0.3) -> float:
    if prior is None:
        return score
    # rank-based scores can be negative (cross-encoder logits): shift by |score| so boosts stay monotone
    return score + abs(score) * strength * (prior - 0.5) * 2
