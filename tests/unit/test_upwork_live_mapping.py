"""Live Upwork adapter: response normalisation, using the shapes recorded from the real server
(docs/upwork_tools_list.json, 2026-11-14) with synthetic values."""
from mcp_servers.marketplace.adapters.upwork_live import _job_from_get, _job_from_search


def test_search_row_hourly():
    j = _job_from_search({"id": "123", "title": "Python API", "budget": "12.00–28.00/hr", "job_type": "hourly",
                          "skills": ["Python"], "description_snippet": "Need an API", "url": "u"})
    assert (j["budget_type"], j["hourly_low"], j["hourly_high"], j["client_id"]) == ("hourly", 12.0, 28.0, "job:123")


def test_search_row_fixed():
    j = _job_from_search({"id": "9", "title": "Site", "budget": "$1,200.00", "job_type": "fixed"})
    assert (j["budget_type"], j["budget_max"]) == ("fixed", 1200.0)


def test_get_job_maps_terms_and_connects():
    out = {"connects_cost": 10, "can_apply": True, "data": {"marketplaceJobPosting": {
        "content": {"title": "T", "description": "D"},
        "contractTerms": {"contractType": "HOURLY", "hourlyContractTerms": {"hourlyBudgetMin": 12, "hourlyBudgetMax": 28}},
        "classification": {"category": {"preferredLabel": "Web, Mobile & Software Dev"}}}}}
    j = _job_from_get("123", out)
    assert j["budget_type"] == "hourly" and j["hourly_high"] == 28 and j["connects_cost"] == 10
    assert j["category"] == "Web, Mobile & Software Dev" and j["client_id"] == "job:123"
