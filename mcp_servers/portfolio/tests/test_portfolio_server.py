import pytest
from mcp.shared.memory import create_connected_server_and_client_session as connect

from mcp_servers.portfolio import server as ps


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("GIGBID_FREELANCER_ID", "fullstack")
    ps._audit = ps.AuditLog(tmp_path / "audit.sqlite3")


async def test_tools_resources_prompts_listed():
    async with connect(ps.mcp._mcp_server) as c:
        tools = {t.name: t for t in (await c.list_tools()).tools}
        assert {"search_evidence", "get_case_study", "list_skills", "get_evidence"} <= set(tools)
        assert all(t.annotations.readOnlyHint for t in tools.values())
        templates = {t.uriTemplate for t in (await c.list_resource_templates()).resourceTemplates}
        assert {"portfolio://case/{case_id}", "portfolio://proposal/{proposal_id}"} <= templates
        prompts = {p.name for p in (await c.list_prompts()).prompts}
        assert "answer_screening_question" in prompts


async def test_search_and_read():
    async with connect(ps.mcp._mcp_server) as c:
        r = (await c.call_tool("search_evidence", {"requirement": "React booking portal", "k": 3})).structuredContent
        assert len(r["results"]) == 3
        first = r["results"][0]
        assert {"type", "source", "date", "uri"} <= set(first)
        cs = (await c.call_tool("get_case_study", {"case_id": "p00"})).structuredContent
        assert cs["evidence_id"] == "fullstack:case:p00" and "fullstack:readme:p00" in cs["related"]
        body = await c.read_resource("portfolio://case/p00")
        assert "Clinic booking portal" in body.contents[0].text
        wins = (await c.call_tool("search_evidence", {"requirement": "SaaS dashboard", "k": 3,
                                                      "filters": {"types": ["proposal"], "outcome": "won"}}))
        assert all(x["outcome"] == "won" for x in wins.structuredContent["results"])
        skills = (await c.call_tool("list_skills", {})).structuredContent["skills"]
        assert any(s["skill"] == "React" for s in skills)


async def test_tenant_isolation():
    async with connect(ps.mcp._mcp_server) as c:
        r = await c.call_tool("get_evidence", {"evidence_id": "ai_ml:case:p00"})
        assert r.isError  # a fullstack caller cannot read the ai_ml persona
