"""MCP client setup + per-agent tool allowlists (A6).

Agents never touch GigSim, Upwork, files or databases directly: every capability is an MCP tool.
`ToolLayer.call(node, "marketplace.get_job", {...})` enforces:
  - the node's allowlist from agent/config.yaml (`tools.allowlist`), filtered by tool name;
  - a per-call timeout and bounded retries with exponential backoff for transient failures;
  - degraded mode: a marketplace that stays down raises MarketplaceDown, the node marks the job
    `degraded`, the UI shows a banner and the worker re-queues the thread.

Backends:
  http   - MultiServerMCPClient (langchain-mcp-adapters) over Streamable HTTP, bearer token + X-Trace-Id
           headers. Used by the API/worker in docker compose and on the deployed host.
  inproc - calls the same FastMCP server objects in-process (same schemas, auth scoping and audit log,
           no network hop). Used by tests and local evaluation runs.
"""
from __future__ import annotations

import asyncio
import json
import os
import random
from typing import Any

from agent import trace
from agent.settings import load_config


class ToolNotAllowed(Exception):
    pass


class ToolCallError(Exception):
    """The tool ran and returned an error (validation, 4xx, conflict). Not retried."""


class MarketplaceDown(Exception):
    pass


SERVERS = ("marketplace", "portfolio", "workspace")


def _tcfg() -> dict:
    return load_config().get("tools", {})


def allowlist(node: str) -> list[str]:
    return _tcfg().get("allowlist", {}).get(node, [])


def _server_module(name: str):
    if name == "marketplace":
        from mcp_servers.marketplace import server
    elif name == "portfolio":
        from mcp_servers.portfolio import server
    elif name == "workspace":
        from mcp_servers.workspace import server
    else:
        raise ValueError(name)
    return server


def _parse(content: Any) -> Any:
    if isinstance(content, (dict, list)) and not (isinstance(content, list) and content and
                                                  isinstance(content[0], dict) and "text" in content[0]):
        return content
    if isinstance(content, list):
        texts = [c.get("text", "") if isinstance(c, dict) else getattr(c, "text", "") for c in content]
        content = "".join(texts)
    try:
        return json.loads(content)
    except (TypeError, ValueError):
        return {"text": content}


class ToolLayer:
    def __init__(self, user_id: str, freelancer_id: str, trace_id: str, backend: str | None = None):
        self.user_id, self.freelancer_id, self.trace_id = user_id, freelancer_id, trace_id
        self.backend = backend or os.getenv("MCP_BACKEND", _tcfg().get("backend", "inproc"))
        self._client = None
        self._tools: dict[str, Any] = {}

    # ---- backends
    async def _inproc(self, server: str, tool: str, args: dict) -> Any:
        from mcp_servers.common.auth import INPROC_CALLER, Caller
        mod = _server_module(server)
        tok = INPROC_CALLER.set(Caller(self.user_id, self.freelancer_id, self.trace_id))
        try:
            res = await mod.mcp.call_tool(tool, args)
        except Exception as e:  # noqa: BLE001 - FastMCP wraps tool and validation errors in ToolError
            if "marketplace_unavailable" in str(e):
                raise MarketplaceDown(str(e)) from e
            raise ToolCallError(str(e)) from e
        finally:
            INPROC_CALLER.reset(tok)
        if isinstance(res, tuple):          # (content blocks, structured)
            return res[1] if res[1] is not None else _parse([c.model_dump() for c in res[0]])
        return _parse([c.model_dump() if hasattr(c, "model_dump") else c for c in res])

    def _connections(self) -> dict:
        token = os.getenv("MCP_CLIENT_TOKEN", "dev-mcp-token")
        headers = {"Authorization": f"Bearer {token}", "X-Trace-Id": self.trace_id,
                   "X-GigBid-User": self.user_id}
        urls = {"marketplace": os.getenv("MARKETPLACE_MCP_URL", "http://localhost:8091/mcp"),
                "portfolio": os.getenv("PORTFOLIO_MCP_URL", "http://localhost:8092/mcp"),
                "workspace": os.getenv("WORKSPACE_MCP_URL", "http://localhost:8093/mcp")}
        return {s: {"transport": "streamable_http", "url": u, "headers": headers} for s, u in urls.items()}

    async def _http(self, server: str, tool: str, args: dict) -> Any:
        from langchain_core.tools import ToolException
        from langchain_mcp_adapters.client import MultiServerMCPClient
        if self._client is None:
            self._client = MultiServerMCPClient(self._connections())
        if server not in self._tools:
            try:
                self._tools[server] = {t.name: t for t in await self._client.get_tools(server_name=server)}
            except Exception as e:  # noqa: BLE001
                if server == "marketplace":
                    raise MarketplaceDown(f"marketplace MCP server unreachable: {e}") from e
                raise
        t = self._tools[server].get(tool)
        if t is None:
            raise ToolCallError(f"{server} has no tool {tool}")
        try:
            out = await t.ainvoke(args)
        except ToolException as e:
            if "marketplace_unavailable" in str(e):
                raise MarketplaceDown(str(e)) from e
            raise ToolCallError(str(e)) from e
        return _parse(out[0] if isinstance(out, tuple) else out)

    # ---- public
    async def call(self, node: str, qualified: str, args: dict | None = None) -> Any:
        if qualified not in allowlist(node):
            raise ToolNotAllowed(f"node {node!r} may not call {qualified!r} (allowlist: {allowlist(node)})")
        server, tool = qualified.split(".", 1)
        args = args or {}
        timeout = float(_tcfg().get("timeout_s", 30))
        retries = int(_tcfg().get("max_retries", 2))
        fn = self._inproc if self.backend == "inproc" else self._http
        last: Exception | None = None
        for attempt in range(retries + 1):
            try:
                res = await asyncio.wait_for(fn(server, tool, args), timeout)
                trace.emit(node, "tool", {"tool": qualified, "args": args, "ok": True,
                                          "result": _summ(res)}, trace_id=self.trace_id)
                return res
            except (ToolCallError, ToolNotAllowed) as e:
                trace.emit(node, "tool", {"tool": qualified, "args": args, "ok": False, "error": str(e)},
                           trace_id=self.trace_id)
                raise
            except (MarketplaceDown, asyncio.TimeoutError, OSError, ConnectionError) as e:
                last = e
            except Exception as e:  # noqa: BLE001 - transport errors from the HTTP client
                last = e
            if attempt < retries:
                await asyncio.sleep(0.5 * 2 ** attempt * (1 + random.random() * 0.2))
        trace.emit(node, "tool", {"tool": qualified, "args": args, "ok": False, "error": str(last)},
                   trace_id=self.trace_id)
        if server == "marketplace":
            raise MarketplaceDown(f"{qualified} failed after {retries + 1} attempts: {last}")
        raise ToolCallError(f"{qualified} failed after {retries + 1} attempts: {last}")

    async def langchain_tools(self, names: list[str]) -> list:
        """LangChain tool objects for a set of qualified names (used by the single-agent baselines)."""
        from langchain_mcp_adapters.client import MultiServerMCPClient
        if self._client is None:
            self._client = MultiServerMCPClient(self._connections())
        out = []
        for server in {n.split(".")[0] for n in names}:
            for t in await self._client.get_tools(server_name=server):
                if f"{server}.{t.name}" in names:
                    out.append(t)
        return out


def _summ(res: Any) -> Any:
    s = json.dumps(res, default=str)
    return res if len(s) < 1500 else s[:1500] + "...(truncated)"
