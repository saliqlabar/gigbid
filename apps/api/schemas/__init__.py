"""Request/response models. The web client in apps/web/src/lib/api is GENERATED from these via OpenAPI."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class LoginIn(BaseModel):
    username: str = Field(min_length=3, max_length=40)
    password: str = Field(min_length=6, max_length=200)


class RegisterIn(LoginIn):
    persona: Literal["ai_ml", "fullstack", "data_analyst"] = "fullstack"


class SavedSearch(BaseModel):
    name: str = Field(max_length=60)
    query: str = Field("", max_length=200)
    category: str = Field("", max_length=60)
    interval_min: int = Field(30, ge=15, le=1440, description="GigSim >= 15 min; live mode is forced to >= 60")
    limit: int = Field(20, ge=1, le=50)


class Settings(BaseModel):
    autonomy: Literal["manual", "auto_skip_low_fit"] = "manual"
    weekly_connects_budget: int = Field(60, ge=0, le=1000)
    hourly_rate_usd: float | None = Field(None, gt=0, le=1000)
    minimum_project_budget_usd: float | None = Field(None, ge=0, le=100000)
    saved_searches: list[SavedSearch] = []
    filters: dict[str, Any] = {}
    marketplace: Literal["gigsim", "upwork_live"] = "gigsim"
    agency_mode: bool = Field(False, description="X1: route each job to the best-fit team member")


class Me(BaseModel):
    user_id: str
    username: str
    persona: str
    settings: Settings
    profile: dict[str, Any]
    connected: dict[str, bool]


class TokenOut(BaseModel):
    token: str
    user: Me


class OnboardingIn(BaseModel):
    persona: Literal["ai_ml", "fullstack", "data_analyst"]
    settings: Settings


class JobCard(BaseModel):
    thread_id: str
    job_id: str
    title: str | None
    status: str
    fit: float | None = None
    risk: float | None = None
    risk_level: str | None = None
    risk_flags: list[str] = []
    scam: bool | None = None
    injection: bool | None = None
    action: str | None = None
    reasons: list[str] | None = None
    price: float | None = None
    bid_type: str | None = None
    connects: int | None = None
    category: str | None = None
    updated: float


class JobDetail(BaseModel):
    thread_id: str
    status: str
    job: dict[str, Any]
    job_spec: dict[str, Any] | None
    risk_report: dict[str, Any] | None
    fit_report: dict[str, Any] | None
    evidence: list[dict[str, Any]]
    decision: dict[str, Any] | None
    price_quote: dict[str, Any] | None
    errors: list[str]
    usage: list[dict[str, Any]]
    pending_approval: bool
    agency_route: dict[str, Any] | None = None


class ApprovalIn(BaseModel):
    action: Literal["approve", "edit", "reject"]
    edited_text: str | None = Field(None, max_length=6000)
    edited_answers: list[dict[str, str]] | None = None
    reason: str | None = Field(None, max_length=500)


class ApprovalOut(BaseModel):
    thread_id: str
    status: str
    pending: dict[str, Any] | None = None
    submission: dict[str, Any] | None = None
    sent: list[dict[str, Any]] | None = None


class Pending(BaseModel):
    thread_id: str
    kind: str
    title: str | None
    status: str
    payload: dict[str, Any] | None
    updated: float


class RegenerateIn(BaseModel):
    paragraph_index: int = Field(ge=0, le=20)
    instruction: str = Field("", max_length=300)


class RegenerateOut(BaseModel):
    paragraph_index: int
    text: str
    full_text: str


class RunEvent(BaseModel):
    ts: float
    node: str
    kind: str
    data: dict[str, Any]
    trace_id: str | None = None


class Checkpoint(BaseModel):
    checkpoint_id: str
    next: list[str]
    step: int | None
    status: str | None
    created_at: str | None


class StatusOut(BaseModel):
    marketplace: str
    marketplace_available: bool
    detail: str = ""
    queued_degraded: int = 0
    worker_queue: bool


class MemoryItem(BaseModel):
    namespace: list[str]
    key: str
    value: dict[str, Any]


class PipelineCard(BaseModel):
    proposal_id: str | None = None
    thread_id: str | None = None
    job_id: str
    title: str | None = None
    stage: Literal["drafted", "submitted", "viewed", "replied", "interview", "hired", "lost"]
    bid_amount: float | None = None
    connects: int | None = None
    updated: str | float | None = None


class ConversationOut(BaseModel):
    conversation_id: str
    job_id: str | None
    title: str | None
    messages: list[dict[str, Any]]
    thread_id: str
    pending: dict[str, Any] | None
    stage: str | None


class Analytics(BaseModel):
    by_category: list[dict[str, Any]]
    connects: dict[str, Any]
    cost_latency: dict[str, Any]
    rejection_reasons: list[dict[str, Any]]
    decisions: dict[str, int]
