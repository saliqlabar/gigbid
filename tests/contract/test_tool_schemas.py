"""MCP tool-schema snapshots (A7). Fails CI when a tool's name, input/output schema or annotations change
unintentionally. After an INTENDED change: UPDATE_SNAPSHOTS=1 pytest tests/contract && commit the json."""
import json
import os
from pathlib import Path

import pytest
from mcp.shared.memory import create_connected_server_and_client_session as connect

from mcp_servers.marketplace import server as market
from mcp_servers.portfolio import server as portfolio
from mcp_servers.workspace import server as workspace

SNAP = Path(__file__).parent / "snapshots"


async def surface(mcp) -> dict:
    async with connect(mcp._mcp_server) as c:
        tools = (await c.list_tools()).tools
        out = {"tools": {t.name: {"input": t.inputSchema, "output": t.outputSchema,
                                  "annotations": t.annotations.model_dump(exclude_none=True) if t.annotations else None}
                         for t in sorted(tools, key=lambda t: t.name)}}
        try:
            out["resource_templates"] = sorted(r.uriTemplate for r in (await c.list_resource_templates()).resourceTemplates)
            out["prompts"] = sorted(p.name for p in (await c.list_prompts()).prompts)
        except Exception:  # noqa: BLE001 - servers without resources/prompts
            pass
    return out


@pytest.mark.parametrize("name,mod", [("marketplace", market), ("portfolio", portfolio), ("workspace", workspace)])
async def test_schema_snapshot(name, mod):
    current = await surface(mod.mcp)
    path = SNAP / f"{name}.json"
    if os.getenv("UPDATE_SNAPSHOTS") or not path.exists():
        path.write_text(json.dumps(current, indent=1, sort_keys=True))
    expected = json.loads(path.read_text())
    assert current == json.loads(json.dumps(current, sort_keys=True))
    assert current == expected, f"{name} MCP tool surface changed; if intended run UPDATE_SNAPSHOTS=1"


async def test_single_letter_skill_is_a_valid_requirement():
    # Regression: the skill tag "R" (the language) crashed the Fit Matcher when requirement had min_length=2.
    async with connect(portfolio.mcp._mcp_server) as c:
        res = await c.call_tool("search_evidence", {"requirement": "R", "k": 1})
    assert not res.isError, res.content
