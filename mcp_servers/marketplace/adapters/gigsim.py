"""GigSim adapter: MarketplacePort over GigSim's REST API.

Failure handling lives here so every caller gets the same behaviour:
- per-request timeout;
- 429 -> wait for Retry-After (capped) and retry;
- 5xx / connection errors -> bounded retries with exponential backoff + jitter;
- after the last attempt -> MarketplaceUnavailable (the UI shows degraded mode and queues work);
- other 4xx -> MarketplaceError with GigSim's message (not retried).
"""
from __future__ import annotations

import asyncio
import os
import random
from typing import Any

import httpx

from agent.marketplace_port import MarketplaceError, MarketplaceUnavailable


class GigSimAdapter:
    name = "gigsim"

    def __init__(self, base_url: str, token: str, timeout_s: float = 10.0, max_retries: int = 3,
                 backoff_base_s: float = 0.5, max_retry_after_s: float = 20.0,
                 transport: httpx.AsyncBaseTransport | None = None):
        self._client = httpx.AsyncClient(base_url=base_url, timeout=timeout_s, transport=transport,
                                         headers={"Authorization": f"Bearer {token}"})
        self.max_retries = max_retries
        self.backoff_base_s = backoff_base_s
        self.max_retry_after_s = max_retry_after_s

    @classmethod
    def from_env(cls, cfg: dict[str, Any]) -> "GigSimAdapter":
        transport = None
        if os.getenv("GIGSIM_INPROCESS") == "1":     # single-process deploy: GigSim runs inside this process
            from gigsim import app as gigsim_app
            transport = httpx.ASGITransport(app=gigsim_app.app)
        return cls(base_url=os.getenv("GIGSIM_BASE_URL", "http://localhost:8081") if transport is None else "http://gigsim",
                   token=os.getenv("GIGSIM_API_TOKEN", "dev-token"),
                   timeout_s=float(cfg.get("timeout_s", 10)),
                   max_retries=int(cfg.get("max_retries", 3)), transport=transport)

    async def _request(self, method: str, path: str, **kw: Any) -> Any:
        last: str = ""
        for attempt in range(self.max_retries + 1):
            try:
                r = await self._client.request(method, path, **kw)
            except (httpx.TransportError, httpx.TimeoutException) as e:
                last = f"{type(e).__name__}: {e}"
            else:
                if r.status_code < 400:
                    return r.json()
                detail = _detail(r)
                if r.status_code == 429:
                    last = "rate limited"
                    if attempt < self.max_retries:
                        wait = min(float(r.headers.get("Retry-After", 1)), self.max_retry_after_s)
                        await asyncio.sleep(wait)
                        continue
                elif r.status_code >= 500:
                    last = f"{r.status_code} {detail}"
                else:
                    raise MarketplaceError(detail, status=r.status_code)
            if attempt < self.max_retries:
                await asyncio.sleep(self.backoff_base_s * 2 ** attempt * (1 + random.random() * 0.2))
        raise MarketplaceUnavailable(f"GigSim unavailable after {self.max_retries + 1} attempts ({last})")

    # ---- read
    async def search_jobs(self, query: str = "", category: str = "", since: str = "",
                          limit: int = 20) -> list[dict[str, Any]]:
        params = {k: v for k, v in {"q": query, "category": category, "since": since,
                                     "limit": limit}.items() if v}
        return (await self._request("GET", "/jobs", params=params))["jobs"]

    async def get_job(self, job_id: str) -> dict[str, Any]:
        return await self._request("GET", f"/jobs/{job_id}")

    async def get_client_history(self, client_id: str) -> dict[str, Any]:
        return await self._request("GET", f"/clients/{client_id}/history")

    async def get_connects_balance(self, freelancer_id: str) -> dict[str, Any]:
        return await self._request("GET", f"/freelancers/{freelancer_id}/connects")

    async def get_market_stats(self, category: str = "") -> dict[str, Any]:
        return await self._request("GET", "/market/stats", params={"category": category} if category else None)

    async def get_policy(self) -> dict[str, Any]:
        return await self._request("GET", "/policy")

    async def list_messages(self, freelancer_id: str, thread_id: str = "") -> list[dict[str, Any]]:
        params = {"freelancer_id": freelancer_id} | ({"thread_id": thread_id} if thread_id else {})
        return await self._request("GET", "/messages", params=params)

    async def list_proposals(self, freelancer_id: str) -> list[dict[str, Any]]:
        return await self._request("GET", "/proposals", params={"freelancer_id": freelancer_id})

    async def list_events(self, freelancer_id: str, after: int = 0) -> list[dict[str, Any]]:
        return await self._request("GET", "/events", params={"freelancer_id": freelancer_id, "after": after})

    # ---- write
    async def create_proposal_draft(self, payload: dict[str, Any], idempotency_key: str) -> dict[str, Any]:
        return await self._request("POST", "/proposals", json=payload | {"idempotency_key": idempotency_key})

    async def submit_proposal(self, proposal_id: str, idempotency_key: str) -> dict[str, Any]:
        return await self._request("POST", f"/proposals/{proposal_id}/submit",
                                   json={"idempotency_key": idempotency_key})

    async def send_message(self, thread_id: str, freelancer_id: str, text: str,
                           idempotency_key: str) -> dict[str, Any]:
        return await self._request("POST", "/messages", json={
            "thread_id": thread_id, "freelancer_id": freelancer_id, "text": text,
            "idempotency_key": idempotency_key})

    async def aclose(self) -> None:
        await self._client.aclose()


def _detail(r: httpx.Response) -> str:
    try:
        body = r.json()
        return str(body.get("error") or body.get("detail") or body)
    except ValueError:
        return r.text[:200]
