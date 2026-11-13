"""Connect a GigBid user to the official Upwork MCP server (A4, live mode) and record its real tool list.

    TOKEN_ENCRYPTION_KEY=... python scripts/upwork_connect.py --user reviewer [--port 8765]

Runs the MCP SDK's OAuth 2.1 flow end to end (discovery, client registration, PKCE, code exchange):
it prints Upwork's authorize URL, you sign in on upwork.com in your own browser (GigBid never sees the
password), Upwork redirects to http://localhost:<port>/callback, and the tokens are stored ENCRYPTED for
that user (apps/api/auth/token_store.py). It then calls tools/list only (no tool is invoked) and writes the
tool names, descriptions, input schemas and annotations to docs/upwork_tools_list.json, which is what the
`marketplace.upwork_tools` map in agent/config.yaml must match. Nothing about the account is written there.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from urllib.parse import parse_qs, urlparse

from agent.settings import ROOT


async def wait_for_code(port: int) -> tuple[str, str | None]:
    """Tiny one-shot HTTP server on localhost that captures ?code=&state= from Upwork's redirect."""
    got: asyncio.Future = asyncio.get_running_loop().create_future()

    async def handle(reader, writer):
        line = (await reader.readline()).decode()
        while (await reader.readline()) not in (b"\r\n", b""):
            pass
        q = parse_qs(urlparse(line.split(" ")[1]).query)
        body = b"GigBid: Upwork connected. You can close this tab." if "code" in q else b"GigBid: no code in callback."
        writer.write(b"HTTP/1.1 200 OK\r\ncontent-type: text/plain\r\ncontent-length: %d\r\n\r\n%s" % (len(body), body))
        await writer.drain()
        writer.close()
        if "code" in q and not got.done():
            got.set_result((q["code"][0], (q.get("state") or [None])[0]))
        elif "error" in q and not got.done():
            got.set_exception(RuntimeError(f"Upwork returned error: {q.get('error')} {q.get('error_description')}"))

    server = await asyncio.start_server(handle, "127.0.0.1", port)
    async with server:
        return await asyncio.wait_for(got, 600)


async def main(a) -> None:
    from mcp import ClientSession
    from mcp.client.auth import OAuthClientProvider
    from mcp.client.streamable_http import streamablehttp_client
    from mcp.shared.auth import OAuthClientMetadata

    from apps.api.auth.token_store import EncryptedTokenStore, UserTokenStorage

    redirect = f"http://localhost:{a.port}/callback"

    async def show(url: str) -> None:
        print("\nOpen this URL in your browser and sign in to Upwork:\n\n" + url + "\n", flush=True)

    auth = OAuthClientProvider(
        server_url=a.url,
        client_metadata=OAuthClientMetadata(client_name="GigBid", redirect_uris=[redirect],
                                            grant_types=["authorization_code", "refresh_token"],
                                            response_types=["code"], token_endpoint_auth_method="none"),
        storage=UserTokenStorage(EncryptedTokenStore(), a.user, "upwork"),
        redirect_handler=show, callback_handler=lambda: wait_for_code(a.port),
        client_metadata_url=a.client_metadata_url)
    async with streamablehttp_client(a.url, auth=auth) as (r, w, _):
        async with ClientSession(r, w) as s:
            info = await s.initialize()
            tools = (await s.list_tools()).tools
    out = {"server": a.url, "server_info": info.serverInfo.model_dump() if info.serverInfo else None,
           "tools": [{"name": t.name, "description": t.description, "annotations": t.annotations.model_dump()
                      if t.annotations else None, "input_schema": t.inputSchema} for t in tools]}
    p = ROOT / "docs" / "upwork_tools_list.json"
    p.write_text(json.dumps(out, indent=1))
    print(f"connected; tokens stored encrypted for user {a.user!r}; {len(tools)} tools -> {p}")
    for t in tools:
        print(f"  {t.name}: {(t.description or '')[:90]}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--user", default="reviewer")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--url", default="https://mcp.upwork.com/mcp")
    ap.add_argument("--client-metadata-url", default=None,
                    help="HTTPS URL of a client-ID metadata document, if Upwork rejects dynamic registration")
    asyncio.run(main(ap.parse_args()))
