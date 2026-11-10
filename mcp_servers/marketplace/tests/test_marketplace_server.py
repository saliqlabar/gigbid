"""Marketplace MCP server through an in-memory MCP client (A7)."""
import json
import sqlite3

import pytest
from mcp.shared.memory import create_connected_server_and_client_session as connect

from agent.marketplace_port import MarketplaceUnavailable
from mcp_servers.marketplace import server as srv

LETTER = "For your booking portal I would reuse the React + Node.js setup from my clinic project. " * 2


@pytest.fixture()
def mserver(gigsim_adapter, tmp_path):
    srv.set_port(gigsim_adapter)
    srv._audit = srv.AuditLog(tmp_path / "audit.sqlite3")
    srv._idem = srv.IdempotencyStore(tmp_path / "idem.sqlite3")
    return srv


def data(res):
    assert not res.isError, res.content
    return res.structuredContent


async def test_tools_and_annotations(mserver):
    async with connect(mserver.mcp._mcp_server) as c:
        tools = {t.name: t for t in (await c.list_tools()).tools}
    assert {"search_jobs", "get_job", "get_client_history", "get_connects_balance", "list_messages",
            "create_proposal_draft", "submit_proposal", "send_message"} <= set(tools)
    assert tools["search_jobs"].annotations.readOnlyHint is True
    sub = tools["submit_proposal"].annotations
    assert sub.readOnlyHint is False and sub.destructiveHint is True and sub.idempotentHint is True
    for name in ("create_proposal_draft", "submit_proposal", "send_message"):
        props = tools[name].inputSchema["properties"]
        assert "idempotency_key" in props and "dry_run" in props
        assert tools[name].outputSchema is not None
    assert tools["create_proposal_draft"].inputSchema["properties"]["cover_letter"]["minLength"] == 50


async def test_draft_dry_run_then_submit_exactly_once(mserver):
    async with connect(mserver.mcp._mcp_server) as c:
        job = data(await c.call_tool("search_jobs", {"limit": 1}))["jobs"][0]
        args = {"job_id": job["job_id"], "cover_letter": LETTER, "bid_amount": 400,
                "idempotency_key": "draft-abc-1"}
        prev = data(await c.call_tool("create_proposal_draft", args | {"dry_run": True}))
        assert prev["status"] == "preview" and prev["would_send"]["cover_letter"] == LETTER
        assert prev["balance_after"] == prev["balance_before"] - prev["connects_cost"]
        assert data(await c.call_tool("list_proposals", {}))["proposals"] == []  # dry run sent nothing

        d1 = data(await c.call_tool("create_proposal_draft", args))
        d2 = data(await c.call_tool("create_proposal_draft", args))
        assert d1["proposal_id"] == d2["proposal_id"] and d2["replayed"] is True

        sp = data(await c.call_tool("submit_proposal", {"proposal_id": d1["proposal_id"],
                                                        "idempotency_key": "submit-abc-1", "dry_run": True}))
        assert sp["connects_cost"] == prev["connects_cost"]
        s1 = data(await c.call_tool("submit_proposal", {"proposal_id": d1["proposal_id"],
                                                        "idempotency_key": "submit-abc-1"}))
        s2 = data(await c.call_tool("submit_proposal", {"proposal_id": d1["proposal_id"],
                                                        "idempotency_key": "submit-abc-1"}))
        assert s1["status"] == "submitted" and s2["replayed"] is True
        bal = data(await c.call_tool("get_connects_balance", {}))
        assert bal["balance"] == prev["balance_before"] - prev["connects_cost"]


async def test_key_reuse_with_other_args_is_rejected(mserver):
    async with connect(mserver.mcp._mcp_server) as c:
        job = data(await c.call_tool("search_jobs", {"limit": 1}))["jobs"][0]
        a = {"job_id": job["job_id"], "cover_letter": LETTER, "bid_amount": 400, "idempotency_key": "dup-key-01"}
        data(await c.call_tool("create_proposal_draft", a))
        r = await c.call_tool("create_proposal_draft", a | {"bid_amount": 999})
        assert r.isError and "IdempotencyConflict" in r.content[0].text


async def test_schema_constraints_enforced(mserver):
    async with connect(mserver.mcp._mcp_server) as c:
        r = await c.call_tool("create_proposal_draft", {"job_id": "x", "cover_letter": "too short",
                                                        "bid_amount": -5, "idempotency_key": "k"})
        assert r.isError


async def test_audit_log_is_append_only_and_masked(mserver):
    async with connect(mserver.mcp._mcp_server) as c:
        data(await c.call_tool("send_message", {"thread_id": "thr_x", "dry_run": True,
                                                "text": "mail me at jo@example.com or +92 300 1234567",
                                                "idempotency_key": "msg-key-01"}))
    rows = mserver.audit().tail(5)
    assert rows[0]["tool"] == "send_message" and rows[0]["trace_id"]
    assert "jo@example.com" not in rows[0]["arguments"] and "[EMAIL]" in rows[0]["arguments"]
    assert "1234567" not in rows[0]["arguments"]
    with sqlite3.connect(mserver.audit().path) as con, pytest.raises(sqlite3.DatabaseError):
        con.execute("delete from audit")


class DownPort:
    name = "gigsim"

    async def get_policy(self):
        raise MarketplaceUnavailable("connection refused")

    async def search_jobs(self, *a, **k):
        raise MarketplaceUnavailable("connection refused")


async def test_degraded_mode_reported(mserver):
    mserver.set_port(DownPort())
    async with connect(mserver.mcp._mcp_server) as c:
        st = data(await c.call_tool("marketplace_status", {}))
        assert st["available"] is False
        r = await c.call_tool("search_jobs", {})
        assert r.isError and "marketplace_unavailable" in r.content[0].text
    row = mserver.audit().tail(1)[0]
    assert row["ok"] == 0 and "marketplace_unavailable" in json.loads(row["result"])["error"]


def test_http_endpoint_requires_bearer_token():
    from starlette.testclient import TestClient
    client = TestClient(srv.http_app())
    assert client.get("/health").status_code == 200
    r = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert r.status_code == 401
    r = client.post("/mcp", json={}, headers={"Authorization": "Bearer wrong"})
    assert r.status_code == 401
