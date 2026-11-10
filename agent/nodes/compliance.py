"""Compliance Guard: enforces the policy pack before a human sees the draft, and again after edits.

Rules come from the platform policy (`marketplace.get_policy`, cached in state) plus the patterns in
data/policy_pack/rules.yaml, so a policy update is a data change. Fixes: contact details and links are
redacted, guarantee sentences removed; off-platform requests, unpaid-work offers, remaining unsupported
claims or an over-length letter BLOCK the draft with a reason shown in the UI.
"""
from __future__ import annotations

import re
from functools import lru_cache

import yaml

from agent.handoffs import ComplianceVerdict, ComplianceViolation
from agent.mcp_client import ToolLayer
from agent.nodes._base import node
from agent.settings import ROOT
from rag.verify import split_sentences


@lru_cache(maxsize=1)
def patterns() -> dict:
    return yaml.safe_load((ROOT / "data" / "policy_pack" / "rules.yaml").read_text())


def check(text: str, policy: dict, unsupported_left: int = 0) -> ComplianceVerdict:
    rules, pat = (policy or {}).get("rules", {}), patterns()
    v: list[ComplianceViolation] = []
    out = text
    for name in ("email", "phone", "url"):
        for m in re.finditer(pat["redact"][name], out, flags=re.I):
            v.append(ComplianceViolation(rule=f"no_contact_details:{name}", excerpt=m.group(0), fix="redacted"))
        out = re.sub(pat["redact"][name], "[removed]", out, flags=re.I)
    if rules.get("no_guarantees", True):
        kept = []
        for s in split_sentences(out):
            if re.search(pat["guarantee"], s, flags=re.I):
                v.append(ComplianceViolation(rule="no_guarantees", excerpt=s[:120], fix="removed"))
            else:
                kept.append(s)
        out = " ".join(kept) if len(kept) != len(split_sentences(out)) else out
    blocked = None
    if rules.get("no_off_platform_contact", True) and re.search(pat["off_platform"], out, flags=re.I):
        m = re.search(pat["off_platform"], out, flags=re.I)
        v.append(ComplianceViolation(rule="no_off_platform_contact", excerpt=m.group(0), fix="blocked"))
        blocked = "asks to communicate or pay outside the platform"
    if rules.get("no_unpaid_trials", True) and re.search(pat["unpaid_work"], out, flags=re.I):
        m = re.search(pat["unpaid_work"], out, flags=re.I)
        v.append(ComplianceViolation(rule="no_unpaid_trials", excerpt=m.group(0), fix="blocked"))
        blocked = blocked or "offers unpaid work"
    max_words = int(rules.get("max_cover_letter_words", 400))
    if len(out.split()) > max_words:
        v.append(ComplianceViolation(rule="max_cover_letter_words", excerpt=f"{len(out.split())} words", fix="blocked"))
        blocked = blocked or f"cover letter longer than {max_words} words"
    if unsupported_left:
        v.append(ComplianceViolation(rule="no_fabricated_experience", excerpt=f"{unsupported_left} unsupported claims",
                                     fix="blocked"))
        blocked = blocked or "contains unsupported claims"
    for extra in pat.get("extra_rules", []) or []:          # rules added by a policy update
        if rules.get(extra["policy_flag"], True) and re.search(extra["pattern"], out, flags=re.I):
            v.append(ComplianceViolation(rule=extra["name"], excerpt=re.search(extra["pattern"], out, re.I).group(0),
                                         fix=extra.get("fix", "blocked")))
            if extra.get("fix", "blocked") == "blocked":
                blocked = blocked or extra["name"]
    return ComplianceVerdict(allowed=blocked is None, blocked_reason=blocked, violations=v, cleaned_text=out.strip(),
                             policy_version=str((policy or {}).get("version", "")))


@node("compliance")
async def compliance(state: dict, tools: ToolLayer) -> dict:
    d = dict(state["proposal_draft"])
    verdict = check(d["cover_letter"], state.get("policy") or {})
    answers = []
    for a in d.get("screening_answers", []):
        va = check(a["answer"], {**(state.get("policy") or {}), "rules": {**(state.get("policy") or {}).get("rules", {}),
                                                                          "max_cover_letter_words": 10_000}})
        verdict.violations += va.violations
        if not va.allowed:
            verdict.allowed, verdict.blocked_reason = False, verdict.blocked_reason or f"screening answer: {va.blocked_reason}"
        answers.append({**a, "answer": va.cleaned_text})
    d["cover_letter"], d["screening_answers"] = verdict.cleaned_text, answers
    out = {"compliance_verdict": verdict.model_dump(), "proposal_draft": d, "drafts": [{"stage": "compliance", **d}]}
    if not verdict.allowed:
        out["status"] = "blocked"
    return out
