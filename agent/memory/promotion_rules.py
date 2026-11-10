"""Explicit promotion rules (R7): the ONLY way anything enters long-term memory.

Raw inputs are *observations* (one per user edit, one per proposal outcome) kept in the app DB.
A rule promotes a lesson into the LangGraph store only when the evidence for it is repeated:

STYLE  (namespace ("style", user)): a style signal extracted from the diff of one edit (e.g. "removed
       the word 'passionate'", "cut the first sentence", "shortened by >25%") is promoted when it appears
       in at least `min_support` of the user's last `window` edits. Promoted rules are written as an
       instruction the Writer reads, with the supporting edit ids.
OUTCOME (namespace ("outcome", user, category)): once a category has at least `min_outcomes` final
       outcomes, we promote the reply-or-better rate and which proposal features (opening type, length
       bucket, evidence types cited) were over-represented among the successes. Never promoted from a
       single outcome, never from raw tool output.
"""
from __future__ import annotations

import difflib
import re
from collections import Counter

from agent.settings import load_config

WATCH_WORDS = ("passionate", "hardworking", "dear", "hiring manager", "expert", "guarantee", "perfect fit",
               "very interested", "i am confident", "kindly", "hello", "hi there")


def _cfg() -> dict:
    return load_config().get("memory", {})


def _sentences(t: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+", (t or "").strip()) if s]


def style_signals(original: str, edited: str) -> list[dict]:
    """What a single edit changed, as discrete, countable signals."""
    sig = []
    o, e = original.lower(), edited.lower()
    for w in WATCH_WORDS:
        if w in o and w not in e:
            sig.append({"key": f"remove_word:{w}", "rule": f"Never use the phrase '{w}'."})
    so, se = _sentences(original), _sentences(edited)
    if so and se and so[0] != se[0] and so[0] not in edited:
        sig.append({"key": "cut_first_sentence", "rule": "Cut the first sentence: start directly with the client's problem."})
    lo, le = len(original.split()), len(edited.split())
    if lo and le / lo < 0.75:
        sig.append({"key": "shorter", "rule": f"Write shorter proposals (about {max(60, int(le / 10) * 10)} words)."})
    if "?" in edited and "?" not in original:
        sig.append({"key": "end_with_question", "rule": "End with one specific question to the client."})
    added = [ln[2:] for ln in difflib.ndiff(o.split(), e.split()) if ln.startswith("+ ")]
    if any(re.fullmatch(r"\d+%?|\$\d+", w) for w in added):
        sig.append({"key": "add_numbers", "rule": "Include concrete numbers from the evidence (results, timelines)."})
    return sig


def promote_style(observations: list[dict]) -> list[dict]:
    """observations: newest first, each {"id", "signals": [...]}. Returns rules to promote."""
    window, support = int(_cfg().get("style_window", 5)), int(_cfg().get("style_min_support", 3))
    recent = observations[:window]
    counts = Counter(s["key"] for o in recent for s in {x["key"]: x for x in o["signals"]}.values())
    rules = {}
    for o in recent:
        for s in o["signals"]:
            if counts[s["key"]] >= support:
                rules.setdefault(s["key"], {"key": s["key"], "rule": s["rule"], "support": counts[s["key"]],
                                            "window": len(recent), "edit_ids": []})["edit_ids"].append(o["id"])
    return list(rules.values())


SUCCESS = {"replied", "interview", "hired"}


def proposal_features(text: str, evidence_ids: list[str]) -> dict:
    first = (_sentences(text) or [""])[0].lower()
    words = len(text.split())
    return {"opening": "question" if first.endswith("?") else "client_problem" if "you" in first or "your" in first
            else "about_me" if first.startswith("i ") else "other",
            "length": "short" if words < 120 else "medium" if words < 220 else "long",
            "evidence_types": sorted({e.split(":")[1] for e in evidence_ids if e.count(":") >= 2})}


def promote_outcomes(category: str, observations: list[dict]) -> dict | None:
    """observations: [{"outcome", "features"}] for one category. None until enough evidence."""
    n = len(observations)
    if n < int(_cfg().get("outcome_min_outcomes", 3)):
        return None
    wins = [o for o in observations if o["outcome"] in SUCCESS]
    rate = len(wins) / n
    lessons = []
    for feat in ("opening", "length"):
        for val in {o["features"][feat] for o in observations}:
            with_v = [o for o in observations if o["features"][feat] == val]
            r = sum(o["outcome"] in SUCCESS for o in with_v) / len(with_v)
            if len(with_v) >= 2 and r - rate >= 0.2:
                lessons.append(f"{feat}={val}: {r:.0%} reply-or-better vs {rate:.0%} overall (n={len(with_v)})")
    return {"category": category, "n": n, "reply_or_better_rate": round(rate, 3), "lessons": lessons}


# kept for the starter-kit test signature
def should_promote_style_edit(edit_count: int, consistent_pattern: bool) -> bool:
    return edit_count >= int(_cfg().get("style_min_support", 3)) and consistent_pattern
