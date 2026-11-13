"""Live Upwork adapter: MarketplacePort over the official Upwork MCP server (A4).

- Connects over Streamable HTTP to https://mcp.upwork.com/mcp with the MCP SDK's OAuth 2.1 client
  (`OAuthClientProvider`); tokens are stored encrypted per user (apps/api/auth/token_store.py). A user
  connects once with `scripts/upwork_connect.py` or Settings -> Connect Upwork.
- Upwork's tools are coarse and action-based (`upwork__find_jobs` with action=search|get, ...). The map
  from port methods to (tool, action) is config (`marketplace.upwork_tools`), checked against the real
  `tools/list` saved in docs/upwork_tools_list.json; responses are normalised into the same shapes the
  GigSim adapter returns, so the graph cannot tell which marketplace it is using.
- Writes never leave GigBid without the user:
  * create_proposal_draft -> `manage_proposals` action=create, which on Upwork only creates a server-side
    PREVIEW (nothing is submitted, no Connects spent). The preview carries Upwork's real Connects cost.
  * submit_proposal NEVER calls `confirm_preview`: it returns `awaiting_user_confirmation` with the job
    URL, and the user submits on Upwork. GigBid cannot spend real Connects.
  * send_message NEVER calls Upwork's `send_message` (that tool sends immediately, there is no draft
    step): the approved text is returned for the user to send on Upwork.
- Polite use: reads only, on the worker's live-mode schedule (>= 60 min, docs/RUNBOOK.md); no browser
  automation, no cookies, no scraping.

Verified 2026-11-14 against the author's own Upwork account: connect, list_accounts, find_jobs search/get,
get_profile connects_balance, list_freelancer_proposals list, get_messages list_rooms (read-only calls).
"""
from __future__ import annotations

import json
import os
import re
from typing import Any

from agent.marketplace_port import MarketplaceError, MarketplaceUnavailable

UPWORK_MCP_URL = "https://mcp.upwork.com/mcp"


class UpworkLiveAdapter:
    name = "upwork_live"

    def __init__(self, user_id: str, tool_map: dict[str, Any], url: str = UPWORK_MCP_URL,
                 redirect_uri: str = "http://localhost:8000/api/auth/upwork/callback"):
        self.user_id = user_id
        self.tool_map = tool_map
        self.url = url
        self.redirect_uri = redirect_uri
        self._session = None
        self._stack = None
        self._org: str | None = None

    @classmethod
    def from_env(cls, cfg: dict[str, Any]) -> "UpworkLiveAdapter":
        return cls(user_id=os.getenv("GIGBID_USER_ID", "reviewer"),
                   tool_map=cfg.get("upwork_tools", {}),
                   url=os.getenv("UPWORK_MCP_URL", UPWORK_MCP_URL),
                   redirect_uri=os.getenv("UPWORK_OAUTH_REDIRECT_URI",
                                          "http://localhost:8000/api/auth/upwork/callback"))

    def _oauth(self):
        from mcp.client.auth import OAuthClientProvider
        from mcp.shared.auth import OAuthClientMetadata

        from apps.api.auth.token_store import EncryptedTokenStore, UserTokenStorage

        async def redirect_handler(url: str) -> None:
            # No stored tokens (or refresh failed): the user must connect from Settings first.
            raise MarketplaceError("upwork_oauth_required: connect Upwork in Settings", status=401)

        return OAuthClientProvider(
            server_url=self.url,
            client_metadata=OAuthClientMetadata(client_name="GigBid", redirect_uris=[self.redirect_uri],
                                                grant_types=["authorization_code", "refresh_token"],
                                                response_types=["code"], token_endpoint_auth_method="none"),
            storage=UserTokenStorage(EncryptedTokenStore(), self.user_id, "upwork"),
            redirect_handler=redirect_handler)

    async def _connect(self):
        if self._session is not None:
            return self._session
        from contextlib import AsyncExitStack

        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client
        stack = AsyncExitStack()
        try:
            r, w, _ = await stack.enter_async_context(streamablehttp_client(self.url, auth=self._oauth()))
            s = await stack.enter_async_context(ClientSession(r, w))
            await s.initialize()
        except MarketplaceError:
            await stack.aclose()
            raise
        except Exception as e:  # noqa: BLE001
            await stack.aclose()
            raise MarketplaceUnavailable(f"Upwork MCP unreachable: {e}") from e
        self._stack, self._session = stack, s
        return s

    async def _raw(self, tool: str, args: dict[str, Any]) -> Any:
        s = await self._connect()
        try:
            res = await s.call_tool(tool, args)
        except Exception as e:  # noqa: BLE001
            await self.aclose()
            raise MarketplaceUnavailable(f"Upwork tool {tool} failed: {e}") from e
        text = "\n".join(c.text for c in res.content if getattr(c, "type", "") == "text")
        if res.isError:
            raise MarketplaceError(f"Upwork {tool}: {text[:300]}", 502)
        return _parse(text)

    async def org(self) -> str:
        if self._org is None:
            acc = await self._raw(self._tool("list_accounts")[0], {})
            items = acc.get("accounts", []) if isinstance(acc, dict) else []
            talent = next((a for a in items if a.get("role") == "TALENT"), items[0] if items else None)
            if not talent:
                raise MarketplaceError("no Upwork account available to this login", 403)
            self._org = talent["org_uid"]
        return self._org

    def _tool(self, method: str) -> tuple[str, str | None]:
        m = self.tool_map.get(method)
        if not m:
            raise MarketplaceError(f"{method} is not supported in live mode (no Upwork tool mapped)", 501)
        return (m["tool"], m.get("action")) if isinstance(m, dict) else (m, None)

    async def _call(self, method: str, **params: Any) -> Any:
        tool, action = self._tool(method)
        return await self._raw(tool, {"action": action, "org_uid": await self.org(),
                                      "params": {k: v for k, v in params.items() if v not in (None, "")}})

    # ---- read
    async def search_jobs(self, query: str = "", category: str = "", since: str = "", limit: int = 20):
        out = await self._call("search_jobs", query=query or None, limit=min(limit, 10), include_full_details=True)
        return [_job_from_search(j) for j in out.get("jobs", [])]

    async def get_job(self, job_id: str):
        return _job_from_get(job_id, await self._call("get_job", id=job_id))

    async def get_client_history(self, client_id: str):
        # Upwork has no client lookup by id: the hiring record comes with the job (find_jobs get), so
        # live jobs carry client_id = "job:<job id>" and we read the record from that job.
        job_id = client_id.split(":", 1)[1] if client_id.startswith("job:") else client_id
        rec = (await self._call("get_job", id=job_id)).get("client_record") or {}
        posted, hires = rec.get("jobs_posted") or 0, rec.get("jobs_with_hires") or 0
        return {"client_id": client_id, "source": "upwork_live", "jobs_posted": posted, "hires": rec.get("contracts_total"),
                "hire_rate": (rec.get("hire_rate_percent") or 0) / 100, "rating": (rec.get("feedback_score") or 0) or None,
                "reviews_count": rec.get("feedback_count"), "total_spent": None, "payment_verified": None,
                "open_jobs": rec.get("open_jobs"), "jobs_with_hires": hires, "reviews": []}

    async def get_connects_balance(self, freelancer_id: str):
        b = (await self._call("get_connects_balance", limit=1)).get("balance") or {}
        return {"freelancer_id": freelancer_id, "balance": b.get("connectsBalance", 0), "weekly_grant": None,
                "source": "upwork_live"}

    async def get_market_stats(self, category: str = ""):
        # Upwork exposes no market statistics tool; pricing falls back to the user's floor and GigBid's
        # historical stats. Returning an empty result is the honest answer.
        return {"category": category or "all", "n_fixed": 0, "n_hourly": 0, "source": "unavailable_in_live_mode"}

    async def get_policy(self):
        import yaml

        from agent.settings import ROOT
        return yaml.safe_load((ROOT / "data" / "policy_pack" / "live_policy.yaml").read_text())

    async def list_messages(self, freelancer_id: str, thread_id: str = ""):
        if thread_id:
            out = await self._call("read_messages", room_id=thread_id, limit=20)
            msgs = (out.get("data") or {}).get("stories") or out.get("messages") or []
            return [{"thread_id": thread_id, "messages": msgs}]
        out = await self._call("list_messages", limit=20)
        return [{"thread_id": r.get("id"), "title": r.get("roomName") or r.get("name"), "raw": r}
                for r in ((out.get("data") or {}).get("rooms") or [])]

    async def list_proposals(self, freelancer_id: str):
        out = await self._call("list_proposals", limit=25)
        edges = (((out.get("data") or {}).get("vendorProposals") or {}).get("edges") or [])
        return [{"proposal_id": e.get("node", {}).get("id"), "status": e.get("node", {}).get("status"),
                 "raw": e.get("node")} for e in edges]

    async def list_events(self, freelancer_id: str, after: int = 0):
        return []  # no event feed in live mode; the worker polls proposals/messages on a slow schedule

    # ---- write: previews only; the user confirms on Upwork
    async def create_proposal_draft(self, payload: dict[str, Any], idempotency_key: str):
        qs = payload.get("screening_questions") or []
        answers = [{"question": q, "answer": a} for q, a in zip(qs, payload.get("screening_answers") or [])]
        out = await self._call("create_proposal_draft", job_reference=payload["job_id"],
                               cover_letter=payload["cover_letter"][:5000], charged_amount=float(payload["bid_amount"]),
                               answers=answers or None)
        prev = out.get("preview") or out
        return {"status": "upwork_preview", "proposal_id": out.get("preview_id") or prev.get("preview_id"),
                "connects_cost": prev.get("connects_cost"), "can_apply": prev.get("can_apply"),
                "note": "Upwork preview only: nothing submitted, no Connects spent."}

    async def submit_proposal(self, proposal_id: str, idempotency_key: str):
        return {"proposal_id": proposal_id, "status": "awaiting_user_confirmation",
                "note": "Confirm and submit on Upwork; GigBid never calls confirm_preview and never spends Connects."}

    async def send_message(self, thread_id: str, freelancer_id: str, text: str, idempotency_key: str):
        return {"thread_id": thread_id, "status": "draft_awaiting_user_confirmation", "text": text,
                "note": "Upwork's send_message sends immediately, so GigBid hands you the approved text to send on Upwork."}

    async def aclose(self) -> None:
        if self._stack is not None:
            try:
                await self._stack.aclose()
            except Exception:  # noqa: BLE001
                pass
        self._stack = self._session = None


def _parse(text: str) -> Any:
    try:
        return json.loads(text)
    except ValueError:
        return {"text": text}


def _rate(budget: str) -> tuple[float | None, float | None]:
    nums = [float(x.replace(",", "")) for x in re.findall(r"[\d,]+(?:\.\d+)?", budget or "")]
    return (nums[0] if nums else None, nums[1] if len(nums) > 1 else (nums[0] if nums else None))


def _job_from_search(j: dict) -> dict:
    hourly = (j.get("job_type") or "").lower().startswith("hour")
    lo, hi = _rate(j.get("budget", ""))
    return {"job_id": str(j.get("id")), "title": j.get("title", ""),
            "description": j.get("description") or j.get("description_snippet", ""), "skills": j.get("skills", []),
            "category": None, "budget_type": "hourly" if hourly else "fixed",
            "budget_max": None if hourly else hi, "hourly_low": lo if hourly else None, "hourly_high": hi if hourly else None,
            "client_id": f"job:{j.get('id')}", "posted_at": j.get("published_date") or j.get("created_date"),
            "url": j.get("url"), "screening_questions": [], "connects_cost": None, "source": "upwork_live"}


def _job_from_get(job_id: str, out: dict) -> dict:
    mp = (out.get("data") or {}).get("marketplaceJobPosting") or {}
    content, terms = mp.get("content") or {}, mp.get("contractTerms") or {}
    hourly_t = terms.get("hourlyContractTerms") or {}
    fixed_t = terms.get("fixedPriceContractTerms") or {}
    hourly = "HOUR" in (terms.get("contractType") or "").upper() or bool(hourly_t)
    amount = (fixed_t.get("amount") or {}).get("rawValue") if isinstance(fixed_t.get("amount"), dict) else fixed_t.get("amount")
    return {"job_id": job_id, "title": content.get("title", ""), "description": content.get("description", ""),
            "category": ((mp.get("classification") or {}).get("category") or {}).get("preferredLabel"),
            "skills": [], "budget_type": "hourly" if hourly else "fixed",
            "budget_max": None if hourly else (float(amount) if amount else None),
            "hourly_low": hourly_t.get("hourlyBudgetMin"), "hourly_high": hourly_t.get("hourlyBudgetMax"),
            "client_id": f"job:{job_id}", "connects_cost": out.get("connects_cost"),
            "connects_balance": out.get("connects_balance"), "can_apply": out.get("can_apply"),
            "applied": out.get("applied"), "url": mp.get("url"),
            "screening_questions": [q.get("question", q) if isinstance(q, dict) else q
                                    for q in (mp.get("questions") or out.get("screening_questions") or [])],
            "source": "upwork_live"}
