"""Fetch Upwork MCP's own tool documentation (read-only `get_tool_help`) with the stored, encrypted tokens.

    TOKEN_ENCRYPTION_KEY=... python scripts/upwork_tool_help.py --user reviewer find_jobs manage_proposals ...

Writes docs/upwork_tool_help.json (Upwork's documentation text only, no account data). Never calls a write tool.
"""
from __future__ import annotations

import argparse
import asyncio
import json

from agent.settings import ROOT


async def main(a) -> None:
    from mcp import ClientSession
    from mcp.client.auth import OAuthClientProvider
    from mcp.client.streamable_http import streamablehttp_client
    from mcp.shared.auth import OAuthClientMetadata

    from apps.api.auth.token_store import EncryptedTokenStore, UserTokenStorage

    async def no_redirect(url: str) -> None:
        raise SystemExit("not connected: run scripts/upwork_connect.py first")

    auth = OAuthClientProvider(
        server_url=a.url, client_metadata=OAuthClientMetadata(
            client_name="GigBid", redirect_uris=["http://localhost:8765/callback"], token_endpoint_auth_method="none",
            grant_types=["authorization_code", "refresh_token"], response_types=["code"]),
        storage=UserTokenStorage(EncryptedTokenStore(), a.user, "upwork"), redirect_handler=no_redirect)
    out = {}
    async with streamablehttp_client(a.url, auth=auth) as (r, w, _):
        async with ClientSession(r, w) as s:
            await s.initialize()
            for name in a.tools:
                res = await s.call_tool("upwork__get_tool_help", {"tool_name": name})
                out[name] = "\n".join(c.text for c in res.content if getattr(c, "type", "") == "text")
                print(f"== {name}: {len(out[name])} chars")
    p = ROOT / "docs" / "upwork_tool_help.json"
    old = json.loads(p.read_text()) if p.exists() else {}
    p.write_text(json.dumps({**old, **out}, indent=1))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--user", default="reviewer")
    ap.add_argument("--url", default="https://mcp.upwork.com/mcp")
    ap.add_argument("tools", nargs="+")
    asyncio.run(main(ap.parse_args()))
