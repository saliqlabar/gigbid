"""The single marketplace interface (R1).

Everything that touches a marketplace goes through `MarketplacePort`. Two adapters implement it:

- `mcp_servers/marketplace/adapters/gigsim.py`      -> GigSim REST simulator (dev + grading)
- `mcp_servers/marketplace/adapters/upwork_live.py` -> official Upwork MCP server (live mode)

`get_marketplace()` picks one from config (`marketplace.mode` in agent/config.yaml, overridable by
the MARKETPLACE_MODE env var). Agents never import an adapter: they call the Marketplace MCP server,
and that server is the only code that holds a port instance.
"""
from __future__ import annotations

import os
from typing import Any, Protocol, runtime_checkable

from agent.settings import load_config


class MarketplaceError(Exception):
    """A marketplace call failed in a way the caller should see (4xx, validation, conflict)."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class MarketplaceUnavailable(MarketplaceError):
    """The marketplace is down or kept failing after bounded retries -> degraded mode."""


@runtime_checkable
class MarketplacePort(Protocol):
    name: str

    # ---- read
    async def search_jobs(self, query: str = "", category: str = "", since: str = "",
                          limit: int = 20) -> list[dict[str, Any]]: ...
    async def get_job(self, job_id: str) -> dict[str, Any]: ...
    async def get_client_history(self, client_id: str) -> dict[str, Any]: ...
    async def get_connects_balance(self, freelancer_id: str) -> dict[str, Any]: ...
    async def get_market_stats(self, category: str = "") -> dict[str, Any]: ...
    async def get_policy(self) -> dict[str, Any]: ...
    async def list_messages(self, freelancer_id: str, thread_id: str = "") -> list[dict[str, Any]]: ...
    async def list_proposals(self, freelancer_id: str) -> list[dict[str, Any]]: ...
    async def list_events(self, freelancer_id: str, after: int = 0) -> list[dict[str, Any]]: ...

    # ---- write (only ever called after human approval; every call carries an idempotency key)
    async def create_proposal_draft(self, payload: dict[str, Any], idempotency_key: str) -> dict[str, Any]: ...
    async def submit_proposal(self, proposal_id: str, idempotency_key: str) -> dict[str, Any]: ...
    async def send_message(self, thread_id: str, freelancer_id: str, text: str,
                           idempotency_key: str) -> dict[str, Any]: ...

    async def aclose(self) -> None: ...


def connects_cost(job: dict[str, Any], policy: dict[str, Any]) -> int:
    """Connects a submission costs: the job's own price plus any policy surcharge.

    Read from the platform policy, so a policy update changes the cost without a code change.
    """
    rules = policy.get("rules", {}) or {}
    return int(job.get("connects_cost", 0) or 0) + int(rules.get("connects_surcharge", 0) or 0)


def get_marketplace(mode: str | None = None) -> MarketplacePort:
    cfg = load_config()["marketplace"]
    mode = mode or os.getenv("MARKETPLACE_MODE") or cfg.get("mode", "gigsim")
    if mode == "gigsim":
        from mcp_servers.marketplace.adapters.gigsim import GigSimAdapter
        return GigSimAdapter.from_env(cfg)
    if mode == "upwork_live":
        from mcp_servers.marketplace.adapters.upwork_live import UpworkLiveAdapter
        return UpworkLiveAdapter.from_env(cfg)
    raise ValueError(f"unknown marketplace mode: {mode!r} (expected gigsim or upwork_live)")
