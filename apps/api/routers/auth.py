"""Sign-in, onboarding, connected accounts (GigSim / Upwork OAuth)."""
from __future__ import annotations

import asyncio
import os

from fastapi import APIRouter, HTTPException, Response

from agent.runner import profile_for
from apps.api.auth.users import check_pw, create_user, find_by_username, get_user, make_token, update_user
from apps.api.deps import COOKIE, CurrentUser
from apps.api.schemas import LoginIn, Me, OnboardingIn, RegisterIn, Settings, TokenOut

router = APIRouter(prefix="/auth", tags=["auth"])


def me_of(user: dict) -> Me:
    prof = profile_for(user["persona"])
    s = user["settings"]
    prof = {**prof, **{k: s[k] for k in ("hourly_rate_usd", "minimum_project_budget_usd") if s.get(k)}}
    connected = {"gigsim": True, "upwork": _upwork_connected(user["user_id"])}
    return Me(user_id=user["user_id"], username=user["username"], persona=user["persona"],
              settings=Settings.model_validate(s), profile=prof, connected=connected)


def _upwork_connected(uid: str) -> bool:
    if not os.getenv("TOKEN_ENCRYPTION_KEY"):
        return False
    from apps.api.auth.token_store import EncryptedTokenStore
    return EncryptedTokenStore().is_connected(uid, "upwork")


def _session(resp: Response, uid: str) -> TokenOut:
    tok = make_token(uid)
    resp.set_cookie(COOKIE, tok, httponly=True, samesite="lax", secure=os.getenv("APP_ENV") == "production",
                    max_age=7 * 24 * 3600)
    return TokenOut(token=tok, user=me_of(get_user(uid)))


@router.post("/login", response_model=TokenOut)
def login(body: LoginIn, resp: Response):
    row = find_by_username(body.username)
    if not row or not check_pw(body.password, row["pw_hash"]):
        raise HTTPException(401, "wrong username or password")
    return _session(resp, row["user_id"])


@router.post("/register", response_model=TokenOut)
def register(body: RegisterIn, resp: Response):
    if find_by_username(body.username):
        raise HTTPException(409, "username taken")
    u = create_user(body.username, body.password, body.persona)
    return _session(resp, u["user_id"])


@router.post("/logout")
def logout(resp: Response):
    resp.delete_cookie(COOKIE)
    return {"ok": True}


@router.get("/me", response_model=Me)
def me(user: dict = CurrentUser):
    return me_of(user)


@router.post("/onboarding", response_model=Me)
def onboarding(body: OnboardingIn, user: dict = CurrentUser):
    return me_of(update_user(user["user_id"], persona=body.persona, settings=body.settings.model_dump()))


_pending: dict[str, asyncio.Future] = {}      # OAuth state -> future the SDK's callback_handler awaits


async def _upwork_flow(user_id: str, url_ready: asyncio.Future) -> list[str]:
    """The MCP SDK's OAuth 2.1 flow against Upwork's MCP server, run as a background task so the browser
    can leave for upwork.com and come back to /auth/upwork/callback. Tokens are stored encrypted."""
    from urllib.parse import parse_qs, urlparse

    from mcp import ClientSession
    from mcp.client.auth import OAuthClientProvider
    from mcp.client.streamable_http import streamablehttp_client
    from mcp.shared.auth import OAuthClientMetadata

    from apps.api.auth.token_store import EncryptedTokenStore, UserTokenStorage
    from mcp_servers.marketplace.adapters.upwork_live import UPWORK_MCP_URL
    code_fut: asyncio.Future = asyncio.get_running_loop().create_future()

    async def redirect_handler(url: str) -> None:
        state = (parse_qs(urlparse(url).query).get("state") or [""])[0]
        _pending[state] = code_fut
        if not url_ready.done():
            url_ready.set_result(url)

    async def callback_handler() -> tuple[str, str | None]:
        return await asyncio.wait_for(code_fut, 600)

    redirect_uri = os.getenv("UPWORK_OAUTH_REDIRECT_URI", "http://localhost:8000/api/auth/upwork/callback")
    auth = OAuthClientProvider(
        server_url=os.getenv("UPWORK_MCP_URL", UPWORK_MCP_URL),
        client_metadata=OAuthClientMetadata(client_name="GigBid", redirect_uris=[redirect_uri],
                                            grant_types=["authorization_code", "refresh_token"],
                                            response_types=["code"], token_endpoint_auth_method="none"),
        storage=UserTokenStorage(EncryptedTokenStore(), user_id, "upwork"),
        redirect_handler=redirect_handler, callback_handler=callback_handler,
        client_metadata_url=os.getenv("UPWORK_CLIENT_METADATA_URL") or None)
    async with streamablehttp_client(os.getenv("UPWORK_MCP_URL", UPWORK_MCP_URL), auth=auth) as (r, w, _):
        async with ClientSession(r, w) as s:
            await s.initialize()
            return [t.name for t in (await s.list_tools()).tools]


@router.get("/upwork/start")
async def upwork_start(user: dict = CurrentUser):
    """Begins the official Upwork MCP OAuth 2.1 flow. Returns the Upwork authorize URL to open, or
    connected=true when stored tokens are still valid. Live mode is optional and never graded."""
    if not os.getenv("TOKEN_ENCRYPTION_KEY"):
        raise HTTPException(501, "live mode not configured on this deployment (TOKEN_ENCRYPTION_KEY unset)")
    url_ready: asyncio.Future = asyncio.get_running_loop().create_future()
    task = asyncio.create_task(_upwork_flow(user["user_id"], url_ready))
    done, _ = await asyncio.wait({task, url_ready}, timeout=30, return_when=asyncio.FIRST_COMPLETED)
    if url_ready in done:
        return {"authorize_url": url_ready.result()}
    if task in done:
        try:
            return {"connected": True, "tools": task.result()}
        except Exception as e:  # noqa: BLE001
            raise HTTPException(502, f"Upwork MCP connection failed: {e}") from e
    task.cancel()
    raise HTTPException(504, "Upwork MCP did not answer in time")


@router.get("/upwork/callback")
async def upwork_callback(code: str = "", state: str = "", error: str = ""):
    """Upwork redirects the browser here; the code goes back to the waiting SDK flow (matched by state)."""
    fut = _pending.pop(state, None)
    if fut is None or fut.done():
        raise HTTPException(400, "unknown or expired OAuth state; start again from Settings")
    if error or not code:
        fut.set_exception(RuntimeError(f"Upwork authorisation failed: {error or 'no code'}"))
        return Response("Upwork connection failed. Close this tab and try again.", media_type="text/plain")
    fut.set_result((code, state))
    return Response("Upwork connected to GigBid. You can close this tab.", media_type="text/plain")
