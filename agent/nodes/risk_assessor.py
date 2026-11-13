"""Risk Assessor: client quality from marketplace history + scam / free-work / off-platform /
injection flags from the post. Score = weighted blend (weights in config `risk`), with hard floors for
high-severity flags. Injection is detected by signatures AND the model; detection only raises a flag:
the post can never change what GigBid does.
"""
from __future__ import annotations

import json

from pydantic import BaseModel, Field

from agent.handoffs import RiskFlag, RiskReport
from agent.llm import structured
from agent.mcp_client import ToolCallError, ToolLayer
from agent.nodes._base import node
from agent.safety.injection import delimit, detect, strip
from agent.settings import load_config


class _RiskLLM(BaseModel):
    scam_likelihood: float = Field(ge=0, le=1)
    flags: list[RiskFlag] = Field(default_factory=list)
    explanation: str = ""


def client_quality(c: dict) -> tuple[float, list[RiskFlag]]:
    flags, q = [], 0.5
    if not c:
        return 0.4, [RiskFlag(kind="low_quality_client", severity="low", evidence="no client history available")]
    if c.get("payment_verified") is False:
        q -= 0.2
        flags.append(RiskFlag(kind="payment_unverified", severity="medium", evidence="payment method not verified"))
    spent = c.get("total_spent_usd") or 0
    q += 0.15 if spent >= 1000 else 0.05 if spent > 0 else -0.1
    rating, reviews = c.get("average_rating"), c.get("reviews_count") or 0
    if rating is not None and reviews >= 3:
        q += 0.15 if rating >= 4.5 else -0.15 if rating < 3.5 else 0
        if rating < 3.5:
            flags.append(RiskFlag(kind="low_quality_client", severity="medium", evidence=f"rating {rating} over {reviews} reviews"))
    hr = c.get("hire_rate")
    if hr is not None:
        q += 0.1 if hr >= 0.5 else -0.1 if hr < 0.2 else 0
    return max(0.0, min(1.0, q)), flags


async def assess(job: dict, client: dict) -> RiskReport:
    w = load_config().get("risk", {})
    q, flags = client_quality(client)
    text = f"{job['title']}\n\n{job.get('description', '')}"
    sigs = detect(text)
    facts = {k: client.get(k) for k in ("country", "total_spent_usd", "reviews_count", "average_rating",
                                        "payment_verified", "hire_rate", "jobs_posted")} if client else {}
    llm = await structured("risk_assessor", "risk_assessor",
                           {"client": json.dumps(facts), "job_post": delimit(strip(text)[0], "job_post")}, _RiskLLM)
    flags += llm.flags
    if sigs and not any(f.kind == "injection" for f in flags):
        flags.append(RiskFlag(kind="injection", severity="high", evidence=sigs[0][:160]))
    injection = any(f.kind == "injection" for f in flags)
    score = (w.get("w_scam", 0.65) * llm.scam_likelihood + w.get("w_client", 0.25) * (1 - q)
             + (w.get("w_injection", 0.10) if injection else 0))
    if any(f.severity == "high" and f.kind in ("scam", "off_platform", "free_work") for f in flags):
        score = max(score, w.get("high_flag_floor", 0.75))
    score = round(min(1.0, score), 3)
    scam = llm.scam_likelihood >= w.get("scam_threshold", 0.6) or any(
        f.kind == "scam" and f.severity == "high" for f in flags)
    level = "high" if score >= 0.7 else "medium" if score >= 0.4 else "low"
    return RiskReport(score=score, level=level, flags=flags, client_quality=round(q, 3), scam=scam,
                      injection_detected=injection, explanation=llm.explanation)


@node("risk_assessor")
async def risk_assessor(state: dict, tools: ToolLayer) -> dict:
    job = state["job"]
    client = state.get("client") or {}
    if not client and job.get("client_id"):
        try:
            client = await tools.call("risk_assessor", "marketplace.get_client_history", {"client_id": job["client_id"]})
        except ToolCallError:
            client = {}
    rep = await assess(job, client)
    return {"risk_report": rep.model_dump(), "client": client, "analyses": {"risk": "ok"}}
