"""Evaluation harness: an isolated, reproducible GigSim (in-process, temp DB, all jobs published, no random
5xx) behind the real Marketplace MCP server, plus graph construction with an in-memory checkpointer.
Import this BEFORE anything that imports gigsim.app (it sets GigSim's env)."""
from __future__ import annotations

import os
import tempfile

_tmp = tempfile.mkdtemp(prefix="gigbid-eval-")
for k, v in {"GIGSIM_DB": f"{_tmp}/gigsim.sqlite3", "GIGSIM_ERROR_RATE": "0", "GIGSIM_RATE_LIMIT_PER_MIN": "1000000",
             "GIGSIM_UNPUBLISHED_TAIL": "0", "GIGSIM_API_TOKENS": "dev-token", "MCP_BACKEND": "inproc",
             "GIGBID_SQLITE": f"{_tmp}/app.sqlite3", "GIGBID_AUDIT_DB": f"{_tmp}/audit.sqlite3",
             "GIGBID_IDEMPOTENCY_DB": f"{_tmp}/idem.sqlite3", "WORKSPACE_DB": f"{_tmp}/ws.sqlite3",
             "GIGBID_STATE_DIR": f"{_tmp}/state"}.items():
    os.environ[k] = v

import httpx  # noqa: E402

from gigsim import app as gigsim_app  # noqa: E402
from mcp_servers.marketplace import server as msrv  # noqa: E402
from mcp_servers.marketplace.adapters.gigsim import GigSimAdapter  # noqa: E402

TMP = _tmp


def fresh_gigsim() -> GigSimAdapter:
    gigsim_app.init_db(reset=True)
    a = GigSimAdapter("http://gigsim", "dev-token", backoff_base_s=0, transport=httpx.ASGITransport(app=gigsim_app.app))
    msrv.set_port(a)
    msrv._limiter = msrv.PerUserRateLimiter(1_000_000)
    return a


def analysis_graph():
    """The production bid graph, paused before drafting (decision + price are complete at that point)."""
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.store.memory import InMemoryStore

    from agent.graphs.bid_graph import build_bid_graph
    from agent.runtime import set_store
    store = InMemoryStore()
    set_store(store)
    g = build_bid_graph(checkpointer=InMemorySaver(), store=store)
    return g.with_config({"recursion_limit": 60}), store


def full_graph():
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.store.memory import InMemoryStore

    from agent.graphs.bid_graph import build_bid_graph
    from agent.runtime import set_store
    store = InMemoryStore()
    set_store(store)
    return build_bid_graph(checkpointer=InMemorySaver(), store=store), store
