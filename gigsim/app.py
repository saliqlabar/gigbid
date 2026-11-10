"""GigSim stand-in: a small marketplace simulator (REST API + seed data).

The instructor's GigSim was not shipped with the starter kit, so this is a self-built stand-in that
follows the brief's description: jobs, clients with histories and hidden preferences, Connects,
proposals, messages, rate limits and occasional errors, plus simulated clients whose decision to
reply / interview / hire is made by an LLM (or a deterministic rule in `rule` mode for CI).

Once seeded this directory is treated as frozen: CI checks that nothing under gigsim/ changes.

Run:  uvicorn gigsim.app:app --port 8081
"""
from __future__ import annotations

import json
import os
import random
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import httpx
import yaml
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

ROOT = Path(__file__).parent
SEED_DIR = Path(os.getenv("GIGSIM_SEED_DIR", ROOT / "seed"))
DB_PATH = os.getenv("GIGSIM_DB", str(ROOT / "gigsim.sqlite3"))
TOKENS = set(filter(None, os.getenv("GIGSIM_API_TOKENS", "dev-token").split(",")))
RATE_LIMIT_PER_MIN = int(os.getenv("GIGSIM_RATE_LIMIT_PER_MIN", "60"))
ERROR_RATE = float(os.getenv("GIGSIM_ERROR_RATE", "0.02"))
CLIENT_MODE = os.getenv("GIGSIM_CLIENT_MODE", "rule")  # llm | rule
LLM_BASE_URL = os.getenv("GIGSIM_LLM_BASE_URL", "http://localhost:11434/v1")
LLM_MODEL = os.getenv("GIGSIM_LLM_MODEL", "qwen3:4b")
LLM_KEY = os.getenv("GIGSIM_LLM_API_KEY", "ollama")

app = FastAPI(title="GigSim (stand-in)", version="0.1.0")
_lock = threading.Lock()
_rng = random.Random(int(os.getenv("GIGSIM_SEED", "4016")))


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------- storage
def db() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH, check_same_thread=False)
    con.row_factory = sqlite3.Row
    return con


SCHEMA = """
create table if not exists jobs(job_id text primary key, data text, published integer, published_at text);
create table if not exists clients(client_id text primary key, data text);
create table if not exists freelancers(freelancer_id text primary key, connects integer, weekly_grant integer);
create table if not exists proposals(proposal_id text primary key, job_id text, freelancer_id text, status text,
  data text, created_at text, updated_at text);
create table if not exists idempotency(key text primary key, response text);
create table if not exists threads(thread_id text primary key, proposal_id text, job_id text, client_id text, freelancer_id text);
create table if not exists messages(message_id text primary key, thread_id text, sender text, text text, created_at text);
create table if not exists events(seq integer primary key autoincrement, freelancer_id text, kind text, data text, created_at text);
"""


def init_db(reset: bool = False) -> None:
    if reset and Path(DB_PATH).exists():
        Path(DB_PATH).unlink()
    con = db()
    con.executescript(SCHEMA)
    if con.execute("select count(*) from jobs").fetchone()[0] == 0 and (SEED_DIR / "jobs.json").exists():
        seed_jobs = json.loads((SEED_DIR / "jobs.json").read_text())
        clients = json.loads((SEED_DIR / "clients.json").read_text())
        # Everything except the last N stream jobs is visible at start; /admin/publish releases the rest.
        tail = int(os.getenv("GIGSIM_UNPUBLISHED_TAIL", "200"))
        live = [j for j in seed_jobs if not j.get("historical")]
        hidden = {j["job_id"] for j in live[len(live) - tail:]} if tail else set()
        for j in seed_jobs:
            pub = 0 if j["job_id"] in hidden else 1
            con.execute("insert into jobs values(?,?,?,?)", (j["job_id"], json.dumps(j), pub, j.get("posted_at") if pub else None))
        for c in clients:
            con.execute("insert into clients values(?,?)", (c["client_id"], json.dumps(c)))
        for fid in ("ai_ml", "fullstack", "data_analyst"):
            con.execute("insert or ignore into freelancers values(?,?,?)", (fid, 80, 80))
    con.commit()
    con.close()


def policy() -> dict:
    p = SEED_DIR / "policy.yaml"
    return yaml.safe_load(p.read_text()) if p.exists() else {"version": "1.0", "rules": {}}


@app.on_event("startup")
def _startup() -> None:
    init_db()


# ---------------------------------------------------------------- auth, rate limit, chaos
_calls: dict[str, list[float]] = {}


def auth(request: Request, authorization: str = Header(default="")) -> str:
    token = authorization.removeprefix("Bearer ").strip()
    if token not in TOKENS:
        raise HTTPException(401, "invalid or missing bearer token")
    t = time.time()
    with _lock:
        window = [x for x in _calls.get(token, []) if t - x < 60]
        if len(window) >= RATE_LIMIT_PER_MIN:
            retry = int(60 - (t - window[0])) + 1
            raise HTTPException(429, "rate limit exceeded", headers={"Retry-After": str(retry)})
        window.append(t)
        _calls[token] = window
    if not request.url.path.startswith("/admin") and _rng.random() < ERROR_RATE:
        raise HTTPException(_rng.choice([500, 502, 503]), "simulated upstream error")
    return token


# ---------------------------------------------------------------- models
class ProposalIn(BaseModel):
    job_id: str
    freelancer_id: str
    cover_letter: str = Field(min_length=50, max_length=5000)
    bid_amount: float = Field(gt=0)
    bid_type: Literal["fixed", "hourly"] = "fixed"
    screening_answers: list[str] = Field(default_factory=list)
    idempotency_key: str = Field(min_length=8)


class SubmitIn(BaseModel):
    idempotency_key: str = Field(min_length=8)


class MessageIn(BaseModel):
    thread_id: str
    freelancer_id: str
    text: str = Field(min_length=1, max_length=4000)
    idempotency_key: str = Field(min_length=8)


def public_job(j: dict) -> dict:
    """Strip the simulator's hidden fields; agents only ever see what a real platform shows."""
    return {k: v for k, v in j.items() if not k.startswith("_")}


def idem_get(con, key: str) -> dict | None:
    r = con.execute("select response from idempotency where key=?", (key,)).fetchone()
    return json.loads(r[0]) if r else None


def idem_put(con, key: str, resp: dict) -> None:
    con.execute("insert into idempotency values(?,?)", (key, json.dumps(resp)))


def emit(con, freelancer_id: str, kind: str, data: dict) -> None:
    con.execute("insert into events(freelancer_id,kind,data,created_at) values(?,?,?,?)",
                (freelancer_id, kind, json.dumps(data), now()))


# ---------------------------------------------------------------- read endpoints
@app.get("/health")
def health():
    return {"status": "ok", "service": "gigsim", "policy_version": policy().get("version")}


@app.get("/policy")
def get_policy(_: str = Depends(auth)):
    return policy()


@app.get("/jobs")
def search_jobs(q: str = "", category: str = "", since: str = "", limit: int = 20, offset: int = 0,
                _: str = Depends(auth)):
    limit = max(1, min(limit, 50))
    con = db()
    rows = con.execute("select data, published_at from jobs where published=1 order by published_at desc").fetchall()
    con.close()
    terms = [t.lower() for t in q.split() if t]
    out = []
    for r in rows:
        j = json.loads(r["data"])
        if j.get("historical"):
            continue
        hay = (j["title"] + " " + j["description"] + " " + " ".join(j.get("skills", []))).lower()
        if terms and not all(t in hay for t in terms):
            continue
        if category and j.get("category") != category:
            continue
        if since and (r["published_at"] or "") <= since:
            continue
        out.append(public_job(j) | {"published_at": r["published_at"]})
    return {"total": len(out), "jobs": out[offset: offset + limit]}


@app.get("/jobs/{job_id}")
def get_job(job_id: str, _: str = Depends(auth)):
    con = db()
    r = con.execute("select data, published_at from jobs where job_id=? and published=1", (job_id,)).fetchone()
    con.close()
    if not r:
        raise HTTPException(404, "job not found")
    return public_job(json.loads(r["data"])) | {"published_at": r["published_at"]}


@app.get("/market/stats")
def market_stats(category: str = "", _: str = Depends(auth)):
    """Price statistics from historical jobs (the brief's ~1,500 historical jobs for pricing)."""
    con = db()
    rows = [json.loads(r[0]) for r in con.execute("select data from jobs where published=1").fetchall()]
    con.close()
    rows = [j for j in rows if j.get("historical") and (not category or j.get("category") == category)]
    fixed = sorted(j["budget_max"] for j in rows if j.get("budget_type") == "fixed" and j.get("budget_max"))
    hourly = sorted(j["hourly_high"] for j in rows if j.get("budget_type") == "hourly" and j.get("hourly_high"))

    def q(xs, p):
        return xs[min(len(xs) - 1, int(p * len(xs)))] if xs else None
    return {"category": category or "all", "n_fixed": len(fixed), "n_hourly": len(hourly),
            "fixed_p25": q(fixed, .25), "fixed_p50": q(fixed, .5), "fixed_p75": q(fixed, .75),
            "hourly_p25": q(hourly, .25), "hourly_p50": q(hourly, .5), "hourly_p75": q(hourly, .75)}


@app.get("/clients/{client_id}/history")
def client_history(client_id: str, _: str = Depends(auth)):
    con = db()
    r = con.execute("select data from clients where client_id=?", (client_id,)).fetchone()
    con.close()
    if not r:
        raise HTTPException(404, "client not found")
    return {k: v for k, v in json.loads(r[0]).items() if not k.startswith("_")}


@app.get("/freelancers/{freelancer_id}/connects")
def connects(freelancer_id: str, _: str = Depends(auth)):
    con = db()
    r = con.execute("select connects, weekly_grant from freelancers where freelancer_id=?", (freelancer_id,)).fetchone()
    con.close()
    if not r:
        raise HTTPException(404, "freelancer not found")
    return {"freelancer_id": freelancer_id, "balance": r["connects"], "weekly_grant": r["weekly_grant"]}


@app.get("/proposals")
def list_proposals(freelancer_id: str, _: str = Depends(auth)):
    con = db()
    rows = con.execute("select * from proposals where freelancer_id=? order by created_at desc", (freelancer_id,)).fetchall()
    con.close()
    return [dict(r) | {"data": json.loads(r["data"])} for r in rows]


@app.get("/messages")
def list_messages(freelancer_id: str, thread_id: str = "", _: str = Depends(auth)):
    con = db()
    threads = con.execute("select * from threads where freelancer_id=?" + (" and thread_id=?" if thread_id else ""),
                          (freelancer_id, thread_id) if thread_id else (freelancer_id,)).fetchall()
    out = []
    for t in threads:
        msgs = con.execute("select * from messages where thread_id=? order by created_at", (t["thread_id"],)).fetchall()
        out.append(dict(t) | {"messages": [dict(m) for m in msgs]})
    con.close()
    return out


@app.get("/events")
def events(freelancer_id: str, after: int = 0, _: str = Depends(auth)):
    con = db()
    rows = con.execute("select * from events where freelancer_id=? and seq>? order by seq", (freelancer_id, after)).fetchall()
    con.close()
    return [dict(r) | {"data": json.loads(r["data"])} for r in rows]


# ---------------------------------------------------------------- write endpoints
def _connects_cost(job: dict) -> int:
    rules = policy().get("rules", {})
    cost = int(job.get("connects_cost", 8))
    cost += int(rules.get("connects_surcharge", 0))
    return cost


@app.post("/proposals")
def create_proposal_draft(body: ProposalIn, _: str = Depends(auth)):
    with _lock:
        con = db()
        if (prev := idem_get(con, "draft:" + body.idempotency_key)):
            con.close()
            return prev
        r = con.execute("select data from jobs where job_id=? and published=1", (body.job_id,)).fetchone()
        if not r:
            con.close()
            raise HTTPException(404, "job not found")
        job = json.loads(r[0])
        pid = "prop_" + uuid.uuid4().hex[:10]
        resp = {"proposal_id": pid, "status": "draft", "job_id": body.job_id,
                "connects_cost": _connects_cost(job), "created_at": now()}
        con.execute("insert into proposals values(?,?,?,?,?,?,?)",
                    (pid, body.job_id, body.freelancer_id, "draft", body.model_dump_json(), now(), now()))
        idem_put(con, "draft:" + body.idempotency_key, resp)
        con.commit()
        con.close()
        return resp


@app.post("/proposals/{proposal_id}/submit")
def submit_proposal(proposal_id: str, body: SubmitIn, _: str = Depends(auth)):
    with _lock:
        con = db()
        if (prev := idem_get(con, "submit:" + body.idempotency_key)):
            con.close()
            return prev
        p = con.execute("select * from proposals where proposal_id=?", (proposal_id,)).fetchone()
        if not p:
            con.close()
            raise HTTPException(404, "proposal not found")
        if p["status"] != "draft":
            con.close()
            raise HTTPException(409, f"proposal already {p['status']}")
        job = json.loads(con.execute("select data from jobs where job_id=?", (p["job_id"],)).fetchone()[0])
        cost = _connects_cost(job)
        bal = con.execute("select connects from freelancers where freelancer_id=?", (p["freelancer_id"],)).fetchone()[0]
        if bal < cost:
            con.close()
            raise HTTPException(402, f"insufficient connects: balance {bal}, cost {cost}")
        con.execute("update freelancers set connects=connects-? where freelancer_id=?", (cost, p["freelancer_id"]))
        con.execute("update proposals set status='submitted', updated_at=? where proposal_id=?", (now(), proposal_id))
        resp = {"proposal_id": proposal_id, "status": "submitted", "connects_spent": cost, "balance": bal - cost}
        idem_put(con, "submit:" + body.idempotency_key, resp)
        emit(con, p["freelancer_id"], "proposal_submitted", {"proposal_id": proposal_id, "job_id": p["job_id"]})
        con.commit()
        con.close()
        return resp


@app.post("/messages")
def send_message(body: MessageIn, _: str = Depends(auth)):
    with _lock:
        con = db()
        if (prev := idem_get(con, "msg:" + body.idempotency_key)):
            con.close()
            return prev
        t = con.execute("select * from threads where thread_id=? and freelancer_id=?", (body.thread_id, body.freelancer_id)).fetchone()
        if not t:
            con.close()
            raise HTTPException(404, "thread not found")
        mid = "msg_" + uuid.uuid4().hex[:10]
        con.execute("insert into messages values(?,?,?,?,?)", (mid, body.thread_id, "freelancer", body.text, now()))
        resp = {"message_id": mid, "thread_id": body.thread_id, "status": "sent"}
        idem_put(con, "msg:" + body.idempotency_key, resp)
        con.commit()
        con.close()
        return resp


# ---------------------------------------------------------------- simulated clients
GENERIC_MARKERS = ("dear hiring manager", "i am very interested", "perfect fit", "i can do this job",
                   "passionate", "hardworking", "to whom it may concern", "i have read your job post")


def rule_decision(client: dict, job: dict, prop: dict) -> dict:
    """Deterministic stand-in for the client LLM (used in CI and when no LLM is configured)."""
    prefs = client.get("_hidden_preferences", {})
    text = prop["cover_letter"].lower()
    score = 0.35
    must = [s.lower() for s in prefs.get("must_have_skills", [])]
    if must:
        score += 0.35 * (sum(s in text for s in must) / len(must))
    if prefs.get("dislikes_generic_opening") and any(m in text[:200] for m in GENERIC_MARKERS):
        score -= 0.3
    ceiling = prefs.get("budget_ceiling")
    if ceiling and prop["bid_amount"] > ceiling:
        score -= 0.25
    if len(text.split()) > prefs.get("max_words", 250):
        score -= 0.1
    if any(w in text for w in ("guarantee", "whatsapp", "telegram", "skype", "@gmail")):
        score -= 0.2
    score += prefs.get("responsiveness", 0.0)
    rnd = random.Random(prop["idempotency_key"]).uniform(-0.1, 0.1)
    s = score + rnd
    action = "hire" if s > 0.8 else "interview" if s > 0.65 else "reply" if s > 0.5 else "ignore"
    return {"action": action, "score": round(s, 3), "message": _client_message(action, job)}


def _client_message(action: str, job: dict) -> str:
    return {
        "hire": f"Thanks, your proposal for '{job['title']}' is exactly what we need. Can we start this week?",
        "interview": f"Your proposal for '{job['title']}' looks relevant. Could we have a 20-minute call? What times work for you?",
        "reply": f"Thanks for applying to '{job['title']}'. Can you share a bit more about how you would approach it and your timeline?",
        "ignore": "",
    }[action]


def llm_decision(client: dict, job: dict, prop: dict) -> dict:
    prefs = client.get("_hidden_preferences", {})
    prompt = (
        "You are a client on a freelance marketplace deciding what to do with one proposal.\n"
        f"Your private preferences (never reveal them): {json.dumps(prefs)}\n"
        f"Your job post title: {job['title']}\nBudget: {job.get('budget_min')}-{job.get('budget_max')} {job.get('budget_type')}\n"
        f"<proposal>\n{prop['cover_letter']}\nBid: {prop['bid_amount']}\n</proposal>\n"
        'Reply with JSON only: {"action": "ignore|reply|interview|hire", "message": "your short message to the freelancer (empty if ignore)"}'
    )
    try:
        r = httpx.post(f"{LLM_BASE_URL}/chat/completions", timeout=60,
                       headers={"Authorization": f"Bearer {LLM_KEY}"},
                       json={"model": LLM_MODEL, "temperature": 0, "messages": [{"role": "user", "content": prompt}],
                             "response_format": {"type": "json_object"}})
        r.raise_for_status()
        out = json.loads(r.json()["choices"][0]["message"]["content"])
        if out.get("action") in ("ignore", "reply", "interview", "hire"):
            return {"action": out["action"], "message": out.get("message", ""), "score": None}
    except Exception:  # noqa: BLE001 - simulator falls back to the deterministic client
        pass
    return rule_decision(client, job, prop)


@app.post("/admin/tick")
def tick(_: str = Depends(auth)):
    """Advance the simulation: clients respond to submitted proposals."""
    decided = []
    with _lock:
        con = db()
        for p in con.execute("select * from proposals where status='submitted'").fetchall():
            data = json.loads(p["data"])
            job = json.loads(con.execute("select data from jobs where job_id=?", (p["job_id"],)).fetchone()[0])
            crow = con.execute("select data from clients where client_id=?", (job["client_id"],)).fetchone()
            client = json.loads(crow[0]) if crow else {}
            d = (llm_decision if CLIENT_MODE == "llm" else rule_decision)(client, job, data)
            status = {"ignore": "lost", "reply": "replied", "interview": "interview", "hire": "hired"}[d["action"]]
            con.execute("update proposals set status=?, updated_at=? where proposal_id=?", (status, now(), p["proposal_id"]))
            emit(con, p["freelancer_id"], "proposal_viewed", {"proposal_id": p["proposal_id"]})
            if d["action"] != "ignore":
                tid = "thr_" + p["proposal_id"][5:]
                con.execute("insert or ignore into threads values(?,?,?,?,?)",
                            (tid, p["proposal_id"], p["job_id"], job["client_id"], p["freelancer_id"]))
                con.execute("insert into messages values(?,?,?,?,?)",
                            ("msg_" + uuid.uuid4().hex[:10], tid, "client", d["message"], now()))
                emit(con, p["freelancer_id"], "client_message", {"thread_id": tid, "proposal_id": p["proposal_id"]})
            emit(con, p["freelancer_id"], "proposal_" + status, {"proposal_id": p["proposal_id"], "job_id": p["job_id"]})
            decided.append({"proposal_id": p["proposal_id"], **d, "status": status})
        con.commit()
        con.close()
    return {"decided": decided}


@app.post("/admin/publish")
def publish(n: int = 10, _: str = Depends(auth)):
    """Release the next n jobs from the live stream (new jobs arriving during the assignment)."""
    with _lock:
        con = db()
        rows = con.execute("select job_id from jobs where published=0 order by rowid limit ?", (n,)).fetchall()
        for r in rows:
            con.execute("update jobs set published=1, published_at=? where job_id=?", (now(), r[0]))
        con.commit()
        con.close()
    return {"published": [r[0] for r in rows]}


@app.post("/admin/reset")
def reset(_: str = Depends(auth)):
    init_db(reset=True)
    return {"status": "reset"}


@app.post("/admin/grant_connects")
def grant(_: str = Depends(auth)):
    con = db()
    con.execute("update freelancers set connects=connects+weekly_grant")
    con.commit()
    con.close()
    return {"status": "granted"}


@app.exception_handler(HTTPException)
async def _http_exc(request: Request, exc: HTTPException):
    return JSONResponse({"error": exc.detail, "status": exc.status_code}, status_code=exc.status_code,
                        headers=getattr(exc, "headers", None))
