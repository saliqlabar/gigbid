from agent.nodes.decide import decide_rules


def test_missing_risk_report_never_bids():
    # A job that would be a clear bid on fit alone must not be bid on when the Risk Assessor crashed.
    job = {"budget_type": "fixed", "budget_max": 5000}
    d = decide_rules(job, {}, {}, {"score": 0.95}, {"hourly_rate_usd": 30, "minimum_project_budget_usd": 100})
    assert d.action == "watch" and d.rule == "risk_unavailable"


def test_present_risk_report_still_bids():
    job = {"budget_type": "fixed", "budget_max": 5000}
    d = decide_rules(job, {}, {"score": 0.1, "scam": False}, {"score": 0.95},
                     {"hourly_rate_usd": 30, "minimum_project_budget_usd": 100})
    assert d.action == "bid"
