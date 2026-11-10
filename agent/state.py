"""Typed graph state (C2) with reducers wherever parallel branches write.

Three things are kept apart:
- STATE (this file): what one job / conversation needs right now. Checkpointed in Postgres.
- EXECUTION TRACE: every tool name, argument, result and observation -> the run-event log
  (agent/trace.py, shown in the run viewer) + the MCP audit log + OpenTelemetry traces (Phoenix).
  The state only holds per-node usage totals for budgets.
- LONG-TERM MEMORY: promoted knowledge only, in the LangGraph store (agent/memory/), written by
  agent/memory/promotion_rules.py. Raw tool output never goes there.
"""
from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict


def merge_dicts(a: dict | None, b: dict | None) -> dict:
    return {**(a or {}), **(b or {})}


_SEVERITY = {"dead_letter": 5, "needs_attention": 4, "degraded": 3, "blocked": 2}


def pick_status(a: str | None, b: str | None) -> str | None:
    """Status reducer. Parallel branches may both write a status: a more severe one is kept
    (dead_letter > needs_attention > degraded > blocked > anything else). A value starting with '='
    overrides unconditionally (used when a degraded job is resumed)."""
    if b is None:
        return a
    if b.startswith("="):
        return b[1:]
    return a if _SEVERITY.get(a or "", 0) > _SEVERITY.get(b, 0) else b


class BidState(TypedDict, total=False):
    # identity
    thread_id: str
    user_id: str
    persona: str
    trace_id: str
    # input
    job: dict[str, Any]
    client: dict[str, Any]
    policy: dict[str, Any]
    profile: dict[str, Any]
    # parallel analyses (each branch writes its own key; `analyses` merges with a reducer)
    job_spec: dict[str, Any]
    risk_report: dict[str, Any]
    fit_report: dict[str, Any]
    analyses: Annotated[dict[str, Any], merge_dicts]
    evidence: Annotated[list[dict[str, Any]], operator.add]
    # decision + price
    decision: dict[str, Any]
    price_quote: dict[str, Any]
    # drafting loop
    proposal_draft: dict[str, Any]
    drafts: Annotated[list[dict[str, Any]], operator.add]          # every version, for the UI diff
    verification: dict[str, Any]
    critiques: Annotated[list[dict[str, Any]], operator.add]
    revisions: int
    compliance_verdict: dict[str, Any]
    # approval + outcome
    approval_request: dict[str, Any]
    approvals: Annotated[list[dict[str, Any]], operator.add]
    submission: dict[str, Any]
    outcome: dict[str, Any]
    # control
    status: Annotated[str, pick_status]   # analysing | skipped | watching | drafting | awaiting_approval | submitted | rejected
    #                      | blocked | needs_attention | dead_letter | degraded
    steps: Annotated[int, operator.add]           # increments from every node
    tokens: Annotated[int, operator.add]
    cost_usd: Annotated[float, operator.add]
    usage: Annotated[list[dict[str, Any]], operator.add]
    errors: Annotated[list[str], operator.add]


class ConversationState(TypedDict, total=False):
    thread_id: str
    user_id: str
    persona: str
    trace_id: str
    conversation_id: str          # marketplace thread id
    job: dict[str, Any]
    proposal: dict[str, Any]
    messages: list[dict[str, Any]]
    last_seen_message_id: str
    intent: dict[str, Any]
    slots: list[dict[str, Any]]
    reply_draft: dict[str, Any]
    compliance_verdict: dict[str, Any]
    approval_request: dict[str, Any]
    approvals: Annotated[list[dict[str, Any]], operator.add]
    sent: Annotated[list[dict[str, Any]], operator.add]
    pipeline_stage: str
    status: Annotated[str, pick_status]
    steps: Annotated[int, operator.add]
    tokens: Annotated[int, operator.add]
    cost_usd: Annotated[float, operator.add]
    usage: Annotated[list[dict[str, Any]], operator.add]
    errors: Annotated[list[str], operator.add]


# Backwards-compatible name from the starter kit
GigBidState = BidState
