import pytest
from mcp.shared.memory import create_connected_server_and_client_session as connect

from mcp_servers.workspace import server as ws


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_DB", str(tmp_path / "ws.sqlite3"))
    ws._audit = ws.AuditLog(tmp_path / "audit.sqlite3")


async def test_slots_hold_and_draft():
    async with connect(ws.mcp._mcp_server) as c:
        slots = (await c.call_tool("get_free_slots", {"max_slots": 3})).structuredContent["slots"]
        assert len(slots) == 3
        args = {"start": slots[0]["start"], "title": "Interview: clinic portal", "idempotency_key": "hold-0001"}
        h1 = (await c.call_tool("create_interview_hold", args)).structuredContent
        h2 = (await c.call_tool("create_interview_hold", args)).structuredContent
        assert h1["status"] == "tentative" and h2["replayed"] and h1["event_id"] == h2["event_id"]
        again = (await c.call_tool("get_free_slots", {"max_slots": 3})).structuredContent["slots"]
        assert slots[0]["start"] not in [s["start"] for s in again]  # held slot no longer offered
        clash = await c.call_tool("create_interview_hold", args | {"idempotency_key": "hold-0002"})
        assert clash.isError
        d = (await c.call_tool("create_email_draft", {"to": "client@example.com", "subject": "Interview",
                                                      "body": "Hi", "idempotency_key": "draft-0001"}))
        assert d.structuredContent["status"] == "draft"
