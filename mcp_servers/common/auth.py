"""Bearer-token auth for the MCP servers' Streamable HTTP endpoint, and per-request identity.

MCP_TOKENS="tokenA=user_a:fullstack,tokenB=user_b:ai_ml" maps a bearer token to (user_id,
freelancer_id). The ASGI middleware rejects unknown tokens with 401 before MCP sees the request; tools
then resolve the caller from the request headers, so every call is scoped to that user's tenant.
Over stdio / in-memory (a local host such as Claude Desktop) the caller is GIGBID_USER_ID.
"""
from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from typing import Any

from starlette.responses import JSONResponse


@dataclass(frozen=True)
class Caller:
    user_id: str
    freelancer_id: str
    trace_id: str


def token_table() -> dict[str, tuple[str, str]]:
    raw = os.getenv("MCP_TOKENS", "dev-mcp-token=reviewer:fullstack")
    out = {}
    for item in filter(None, (x.strip() for x in raw.split(","))):
        tok, _, who = item.partition("=")
        user, _, fid = who.partition(":")
        out[tok] = (user, fid or user)
    return out


class BearerAuthMiddleware:
    def __init__(self, app: Any, open_paths: tuple[str, ...] = ("/health",)):
        self.app = app
        self.open_paths = open_paths

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["path"] not in self.open_paths:
            headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
            tok = headers.get("authorization", "").removeprefix("Bearer ").strip()
            if tok not in token_table():
                await JSONResponse({"error": "invalid or missing bearer token"}, status_code=401,
                                   headers={"WWW-Authenticate": "Bearer"})(scope, receive, send)
                return
        await self.app(scope, receive, send)


def caller_from_ctx(ctx: Any) -> Caller:
    req = None
    meta = None
    try:
        req = ctx.request_context.request
        meta = ctx.request_context.meta
    except (AttributeError, ValueError, LookupError):
        pass
    trace_id = None
    if meta is not None:
        trace_id = (getattr(meta, "model_extra", None) or {}).get("trace_id")
    if req is not None and getattr(req, "headers", None) is not None:
        tok = req.headers.get("authorization", "").removeprefix("Bearer ").strip()
        user, fid = token_table().get(tok, ("anonymous", "anonymous"))
        trace_id = trace_id or req.headers.get("x-trace-id")
    else:
        user = os.getenv("GIGBID_USER_ID", "local")
        fid = os.getenv("GIGBID_FREELANCER_ID", "fullstack")
    return Caller(user, fid, trace_id or "tr_" + uuid.uuid4().hex[:16])
