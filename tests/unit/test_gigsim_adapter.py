import httpx
import pytest

from agent.marketplace_port import (MarketplaceError, MarketplacePort, MarketplaceUnavailable,
                                    connects_cost)
from mcp_servers.marketplace.adapters.gigsim import GigSimAdapter

LETTER = "I built a React and Node.js booking portal for a clinic; here is how I would start. " * 2


def test_adapter_satisfies_port(gigsim_adapter):
    assert isinstance(gigsim_adapter, MarketplacePort)


async def test_read_paths(gigsim_adapter):
    jobs = await gigsim_adapter.search_jobs(limit=5)
    assert len(jobs) == 5 and not any(k.startswith("_") for k in jobs[0])  # hidden fields stripped
    job = await gigsim_adapter.get_job(jobs[0]["job_id"])
    hist = await gigsim_adapter.get_client_history(job["client_id"])
    assert "_hidden_preferences" not in hist
    bal = await gigsim_adapter.get_connects_balance("fullstack")
    assert bal["balance"] == 80


async def test_submit_is_idempotent(gigsim_adapter):
    job = (await gigsim_adapter.search_jobs(limit=1))[0]
    payload = {"job_id": job["job_id"], "freelancer_id": "fullstack", "cover_letter": LETTER,
               "bid_amount": 500, "bid_type": "fixed"}
    d1 = await gigsim_adapter.create_proposal_draft(payload, "key-draft-1")
    d2 = await gigsim_adapter.create_proposal_draft(payload, "key-draft-1")
    assert d1["proposal_id"] == d2["proposal_id"]
    s1 = await gigsim_adapter.submit_proposal(d1["proposal_id"], "key-submit-1")
    s2 = await gigsim_adapter.submit_proposal(d1["proposal_id"], "key-submit-1")
    assert s1 == s2
    bal = await gigsim_adapter.get_connects_balance("fullstack")
    assert bal["balance"] == 80 - s1["connects_spent"]  # Connects spent once


async def test_connects_cost_reads_policy(gigsim_adapter):
    assert connects_cost({"connects_cost": 8}, {"rules": {"connects_surcharge": 2}}) == 10
    policy = await gigsim_adapter.get_policy()
    assert "rules" in policy


def _flaky(statuses):
    calls = {"n": 0}

    def handler(request):
        s = statuses[min(calls["n"], len(statuses) - 1)]
        calls["n"] += 1
        headers = {"Retry-After": "0"} if s == 429 else {}
        return httpx.Response(s, json={"jobs": [], "error": "x"}, headers=headers)
    return handler, calls


async def test_retries_5xx_and_429_then_succeeds():
    handler, calls = _flaky([503, 429, 200])
    a = GigSimAdapter("http://x", "t", backoff_base_s=0, transport=httpx.MockTransport(handler))
    assert await a.search_jobs() == []
    assert calls["n"] == 3


async def test_gives_up_with_unavailable():
    handler, calls = _flaky([502])
    a = GigSimAdapter("http://x", "t", max_retries=2, backoff_base_s=0,
                      transport=httpx.MockTransport(handler))
    with pytest.raises(MarketplaceUnavailable):
        await a.search_jobs()
    assert calls["n"] == 3  # bounded


async def test_4xx_not_retried():
    handler, calls = _flaky([404])
    a = GigSimAdapter("http://x", "t", backoff_base_s=0, transport=httpx.MockTransport(handler))
    with pytest.raises(MarketplaceError) as e:
        await a.get_job("nope")
    assert e.value.status == 404 and calls["n"] == 1


async def test_connection_refused_is_unavailable():
    def handler(request):
        raise httpx.ConnectError("refused")
    a = GigSimAdapter("http://x", "t", max_retries=1, backoff_base_s=0,
                      transport=httpx.MockTransport(handler))
    with pytest.raises(MarketplaceUnavailable):
        await a.get_policy()
