"""Decision node: skip / watch / bid from the three parallel reports. Every threshold is config
(`thresholds`), and the rule that fired is recorded so the UI can explain the decision."""
from __future__ import annotations

from agent.handoffs import BidDecision
from agent.mcp_client import ToolLayer
from agent.nodes._base import node
from agent.settings import load_config


def decide_rules(job: dict, spec: dict, risk: dict, fit: dict, profile: dict, duplicate_of: str | None = None
                 ) -> BidDecision:
    t = load_config()["thresholds"]
    rate = float(profile.get("hourly_rate_usd", 30))
    floor_fixed = float(profile.get("minimum_project_budget_usd", 0))
    if duplicate_of:
        return BidDecision(action="skip", reasons=[f"near-duplicate of {duplicate_of}"], rule="duplicate")
    if not risk:  # fail closed: a crashed Risk Assessor must not silently remove the scam veto
        return BidDecision(action="watch", reasons=["risk check failed; review this job yourself"],
                           rule="risk_unavailable")
    if risk.get("scam"):
        return BidDecision(action="skip", reasons=["likely scam: " + (risk.get("explanation") or "")], rule="scam")
    if risk.get("score", 0) >= t["maximum_risk_score"]:
        return BidDecision(action="skip", reasons=[f"risk {risk['score']:.2f} >= {t['maximum_risk_score']}"],
                           rule="maximum_risk_score")
    if job.get("budget_type") == "fixed" and job.get("budget_max") and job["budget_max"] < floor_fixed:
        return BidDecision(action="skip", reasons=[f"budget ${job['budget_max']:.0f} below my minimum ${floor_fixed:.0f}"],
                           rule="minimum_project_budget")
    if job.get("budget_type") == "hourly" and job.get("hourly_high") and \
            job["hourly_high"] < t["minimum_hourly_ratio"] * rate:
        return BidDecision(action="skip", reasons=[f"top rate ${job['hourly_high']:.0f}/h below "
                                                   f"{t['minimum_hourly_ratio']:.0%} of my ${rate:.0f}/h"],
                           rule="minimum_hourly_ratio")
    f = float(fit.get("score", 0))
    if f < t["watch_fit_score"]:
        return BidDecision(action="skip", reasons=[f"fit {f:.2f} < {t['watch_fit_score']}"] + fit.get("reasons", [])[:3],
                           rule="watch_fit_score")
    if f < t["minimum_fit_score"]:
        return BidDecision(action="watch", reasons=[f"fit {f:.2f} between {t['watch_fit_score']} and "
                                                    f"{t['minimum_fit_score']}"], rule="minimum_fit_score")
    return BidDecision(action="bid", reasons=[f"fit {f:.2f}, risk {risk.get('score', 0):.2f}"], rule="bid")


@node("decide")
async def decide(state: dict, tools: ToolLayer) -> dict:
    d = decide_rules(state["job"], state.get("job_spec") or {}, state.get("risk_report") or {},
                     state.get("fit_report") or {}, state.get("profile") or {}, state["job"].get("duplicate_of"))
    status = {"bid": "drafting", "watch": "watching", "skip": "skipped"}[d.action]
    if d.rule == "risk_unavailable":
        status = "needs_attention"
    return {"decision": d.model_dump(), "status": status}
