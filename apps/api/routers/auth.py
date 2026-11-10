"""Sign-in, onboarding, connected accounts (GigSim / Upwork OAuth)."""
from __future__ import annotations

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


@router.get("/upwork/start")
async def upwork_start(user: dict = CurrentUser):
    """Begins the official Upwork MCP OAuth 2.1 flow. Live mode is optional and never graded."""
    if not os.getenv("TOKEN_ENCRYPTION_KEY"):
        raise HTTPException(501, "live mode not configured on this deployment (TOKEN_ENCRYPTION_KEY unset)")
    from agent.marketplace_port import MarketplaceError
    from mcp_servers.marketplace.adapters.upwork_live import UpworkLiveAdapter
    a = UpworkLiveAdapter(user_id=user["user_id"], tool_map={})
    try:
        await a.search_jobs()  # pragma: no cover - needs an Upwork account
    except MarketplaceError as e:
        if str(e).startswith("upwork_oauth_required:"):
            return {"authorize_url": str(e).split(":", 1)[1]}
        raise HTTPException(502, str(e)) from e
    return {"connected": True}
