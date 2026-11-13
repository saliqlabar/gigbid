"""Regenerate the first-party rows of docs/TOOLS.md from the MCP servers' own tools/list (A2, A5)."""
from __future__ import annotations

import asyncio

from mcp.shared.memory import create_connected_server_and_client_session as connect

from mcp_servers.marketplace import server as m
from mcp_servers.portfolio import server as p
from mcp_servers.workspace import server as w

AUTH = {"marketplace": "Bearer token (HTTP); per-user rate limit; audit log",
        "portfolio": "Bearer token (HTTP); read-only", "workspace": "Bearer token (HTTP); audit log"}


async def rows() -> list[str]:
    out = []
    for name, mod in (("portfolio", p), ("marketplace", m), ("workspace", w)):
        async with connect(mod.mcp._mcp_server) as c:
            for t in (await c.list_tools()).tools:
                a = t.annotations
                ro = a.readOnlyHint if a and a.readOnlyHint is not None else False
                ann = ", ".join(f"{k}={v}" for k, v in (("readOnly", a.readOnlyHint), ("destructive", a.destructiveHint),
                                                       ("idempotent", a.idempotentHint)) if a and v is not None)
                out.append(f"| `{t.name}` | {name} | {'read' if ro else 'write'} | {ann} | {AUTH[name]} |")
    return out


if __name__ == "__main__":
    print("\n".join(asyncio.run(rows())))
