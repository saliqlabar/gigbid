"""Structured hand-offs (C3). Agents exchange these Pydantic objects, never free text, for anything a
later step decides on. Field descriptions double as the JSON schema the LLM nodes are asked to fill.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class JobSpec(BaseModel):
    job_id: str
    title: str
    summary: str = Field("", description="One sentence: what the client actually needs")
    deliverables: list[str] = Field(default_factory=list, description="Concrete things to deliver")
    required_skills: list[str] = Field(default_factory=list, description="Skills/tools the job needs")
    nice_to_have: list[str] = Field(default_factory=list)
    budget_type: Literal["fixed", "hourly", "unknown"] = "unknown"
    budget_min: float | None = None
    budget_max: float | None = None
    timeline: str | None = Field(None, description="Deadline or duration if stated")
    screening_questions: list[str] = Field(default_factory=list)
    hidden_needs: list[str] = Field(default_factory=list, description="Needs implied but not stated")
    client_specifics: list[str] = Field(default_factory=list,
                                        description="Details unique to this client (domain, users, pain) "
                                                    "a personalised proposal should mention")

    @property
    def requirements(self) -> list[str]:
        """What the Fit Matcher / Writer must prove: skills first, then deliverables (deduplicated)."""
        seen, out = set(), []
        for r in self.required_skills + self.deliverables:
            k = r.strip().lower()
            if k and k not in seen:
                seen.add(k)
                out.append(r.strip())
        return out[:8]


class RiskFlag(BaseModel):
    kind: Literal["scam", "free_work", "off_platform", "injection", "low_quality_client", "unrealistic_budget",
                  "payment_unverified", "other"]
    severity: Literal["low", "medium", "high"]
    evidence: str = Field(description="Short quote or fact that triggered the flag")


class RiskReport(BaseModel):
    score: float = Field(ge=0, le=1, description="0 = safe, 1 = certainly a scam / do not engage")
    level: Literal["low", "medium", "high"] = "low"
    flags: list[RiskFlag] = Field(default_factory=list)
    client_quality: float = Field(0.5, ge=0, le=1)
    scam: bool = False
    injection_detected: bool = False
    explanation: str = ""


class RequirementMatch(BaseModel):
    requirement: str
    strength: Literal["strong", "partial", "none"]
    evidence_ids: list[str] = Field(default_factory=list)
    reason: str = ""


class FitReport(BaseModel):
    score: float = Field(ge=0, le=1)
    matches: list[RequirementMatch] = Field(default_factory=list)
    matched_skills: list[str] = Field(default_factory=list)
    missing_skills: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)


class BidDecision(BaseModel):
    action: Literal["bid", "watch", "skip"]
    reasons: list[str]
    rule: str = Field(description="Which config rule decided it")


class PriceQuote(BaseModel):
    bid_type: Literal["fixed", "hourly"]
    amount: float
    band_low: float | None = None
    band_high: float | None = None
    connects_cost: int = 0
    connects_balance: int | None = None
    connects_worth_it: bool = True
    expected_value_usd: float | None = None
    rationale: list[str] = Field(default_factory=list)


class ScreeningAnswer(BaseModel):
    question: str
    answer: str


class ProposalDraft(BaseModel):
    job_id: str = ""
    cover_letter: str = Field(description="The cover letter. Cite evidence after each factual claim like "
                                          "[fullstack:case:p00]. No greeting line, no contact details.")
    screening_answers: list[ScreeningAnswer] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list, description="Every evidence id cited")
    revision: int = 0
    status: str = "pending_review"


class ClaimCheck(BaseModel):
    claim: str
    status: Literal["supported", "partly_supported", "unsupported", "not_a_claim"]
    evidence_ids: list[str] = Field(default_factory=list)
    note: str = ""
    action: Literal["kept", "rewritten", "removed"] = "kept"


class Verification(BaseModel):
    claims: list[ClaimCheck] = Field(default_factory=list)
    unsupported_before: int = 0
    unsupported_after: int = 0
    revised_text: str = ""


class Critique(BaseModel):
    approve: bool
    grounding: int = Field(ge=1, le=5)
    personalisation: int = Field(ge=1, le=5)
    length_ok: bool = True
    generic_opening: bool = False
    issues: list[str] = Field(default_factory=list)
    revision_instructions: str = ""


class ComplianceViolation(BaseModel):
    rule: str
    excerpt: str
    fix: Literal["redacted", "removed", "blocked"]


class ComplianceVerdict(BaseModel):
    allowed: bool
    blocked_reason: str | None = None
    violations: list[ComplianceViolation] = Field(default_factory=list)
    cleaned_text: str = ""
    policy_version: str = ""


class ApprovalDecision(BaseModel):
    action: Literal["approve", "edit", "reject"]
    edited_text: str | None = None
    edited_answers: list[ScreeningAnswer] | None = None
    reason: str | None = None
    user_id: str = "reviewer"


class MessageIntent(BaseModel):
    intent: Literal["question", "negotiation", "interview_request", "scam_signal", "hire", "other"]
    confidence: float = Field(ge=0, le=1)
    summary: str = ""
    injection_detected: bool = False


class ReplyDraft(BaseModel):
    text: str
    proposed_slots: list[str] = Field(default_factory=list)
    needs_human_input: list[str] = Field(default_factory=list,
                                         description="Things the freelancer must decide (rate change, scope)")
