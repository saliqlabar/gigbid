"""Users, password hashing (scrypt) and signed session tokens. Tenant = user_id; every query is scoped by it."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
import uuid

from agent import db

_SCHEMA = """create table if not exists users(user_id text primary key, username text unique, pw_hash text,
  persona text, settings text, created real)"""
_ready = False
SESSION_TTL_S = 7 * 24 * 3600

DEFAULT_SETTINGS = {
    "autonomy": "manual",                      # manual | auto_skip_low_fit (never auto-send)
    "weekly_connects_budget": 60,
    "hourly_rate_usd": None, "minimum_project_budget_usd": None,   # None = persona profile value
    "saved_searches": [{"name": "Main feed", "query": "", "category": "", "interval_min": 30, "limit": 20}],
    "filters": {"min_fit": None, "hide_skipped": False},
    "marketplace": "gigsim",
}


def _init():
    global _ready
    if not _ready:
        db.executescript(_SCHEMA)
        _ready = True


def _secret() -> bytes:
    return os.getenv("APP_SECRET_KEY", "dev-insecure-secret-change-me").encode()


def hash_pw(pw: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    h = hashlib.scrypt(pw.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
    return base64.b64encode(salt).decode() + "$" + base64.b64encode(h).decode()


def check_pw(pw: str, stored: str) -> bool:
    salt_b64, _ = stored.split("$", 1)
    return hmac.compare_digest(hash_pw(pw, base64.b64decode(salt_b64)), stored)


def create_user(username: str, password: str, persona: str = "fullstack", user_id: str | None = None) -> dict:
    _init()
    uid = user_id or "u_" + uuid.uuid4().hex[:10]
    db.execute("insert into users values(?,?,?,?,?,?)",
               (uid, username.lower(), hash_pw(password), persona, json.dumps(DEFAULT_SETTINGS), time.time()))
    return get_user(uid)


def get_user(user_id: str) -> dict | None:
    _init()
    rows = db.query("select * from users where user_id=?", (user_id,))
    if not rows:
        return None
    u = rows[0]
    return {"user_id": u["user_id"], "username": u["username"], "persona": u["persona"],
            "settings": {**DEFAULT_SETTINGS, **json.loads(u["settings"] or "{}")}}


def find_by_username(username: str) -> dict | None:
    _init()
    rows = db.query("select * from users where username=?", (username.lower(),))
    return rows[0] if rows else None


def update_user(user_id: str, persona: str | None = None, settings: dict | None = None) -> dict:
    u = get_user(user_id)
    if persona:
        db.execute("update users set persona=? where user_id=?", (persona, user_id))
    if settings is not None:
        db.execute("update users set settings=? where user_id=?", (json.dumps({**u["settings"], **settings}), user_id))
    return get_user(user_id)


def all_users() -> list[dict]:
    _init()
    return [get_user(r["user_id"]) for r in db.query("select user_id from users")]


def make_token(user_id: str) -> str:
    body = base64.urlsafe_b64encode(json.dumps({"u": user_id, "exp": int(time.time()) + SESSION_TTL_S}).encode()).decode()
    sig = hmac.new(_secret(), body.encode(), hashlib.sha256).hexdigest()
    return f"{body}.{sig}"


def read_token(token: str) -> str | None:
    try:
        body, sig = token.rsplit(".", 1)
        if not hmac.compare_digest(sig, hmac.new(_secret(), body.encode(), hashlib.sha256).hexdigest()):
            return None
        data = json.loads(base64.urlsafe_b64decode(body))
        return data["u"] if data["exp"] > time.time() else None
    except Exception:  # noqa: BLE001
        return None


def ensure_reviewer() -> None:
    """Preload the reviewer login (R10/E6) with the full-stack persona. Password from REVIEWER_PASSWORD."""
    _init()
    if not find_by_username("reviewer"):
        create_user("reviewer", os.getenv("REVIEWER_PASSWORD", "reviewer-dev-password"), "fullstack", user_id="reviewer")
