"""Workspace MCP server (A3): calendar slots, tentative interview holds, email drafts.

Backed by a documented LOCAL MOCK (SQLite), as the brief allows: the agent only ever sees these MCP
tools, so swapping in Google Calendar / Gmail later changes this file only. Working hours, slot length
and timezone come from env so a reviewer can see realistic availability.

Run:  python -m mcp_servers.workspace.server            (stdio)
      python -m mcp_servers.workspace.server --http --port 8093
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated
from zoneinfo import ZoneInfo

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field

from mcp_servers.common.audit import AuditLog
from mcp_servers.common.auth import BearerAuthMiddleware, caller_from_ctx

ROOT = Path(__file__).resolve().parents[2]
TZ = ZoneInfo(os.getenv("WORKSPACE_TZ", "Asia/Karachi"))
WORK_START, WORK_END = int(os.getenv("WORKSPACE_WORK_START", "10")), int(os.getenv("WORKSPACE_WORK_END", "18"))

mcp = FastMCP("gigbid-workspace", instructions="Calendar availability, tentative interview holds and "
              "email drafts for the signed-in freelancer. Nothing here sends anything.")
_audit: AuditLog | None = None


def _db() -> sqlite3.Connection:
    path = Path(os.getenv("WORKSPACE_DB", ROOT / "data/processed/workspace.sqlite3"))
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.executescript("""
    create table if not exists events(event_id text primary key, user_id text, start text, end text,
      title text, status text, idempotency_key text, unique(user_id, idempotency_key));
    create table if not exists drafts(draft_id text primary key, user_id text, to_addr text, subject text,
      body text, created_at text, idempotency_key text, unique(user_id, idempotency_key));
    """)
    return con


def audit() -> AuditLog:
    global _audit
    if _audit is None:
        _audit = AuditLog()
    return _audit


class Slot(BaseModel):
    start: str
    end: str


class Slots(BaseModel):
    timezone: str
    slots: list[Slot]


class Hold(BaseModel):
    event_id: str
    start: str
    end: str
    title: str
    status: str
    replayed: bool = False


class EmailDraft(BaseModel):
    draft_id: str
    to: str
    subject: str
    status: str = "draft"
    replayed: bool = False


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True,
                                      openWorldHint=False))
def get_free_slots(
    days_ahead: Annotated[int, Field(ge=1, le=14)] = 5,
    duration_minutes: Annotated[int, Field(ge=15, le=120)] = 30,
    max_slots: Annotated[int, Field(ge=1, le=20)] = 6,
    ctx: Context | None = None,
) -> Slots:
    """Free interview slots in working hours (weekdays), skipping existing holds."""
    c = caller_from_ctx(ctx)
    con = _db()
    busy = [(datetime.fromisoformat(r["start"]), datetime.fromisoformat(r["end"]))
            for r in con.execute("select start,end from events where user_id=? and status!='cancelled'",
                                 (c.user_id,))]
    con.close()
    now = datetime.now(TZ)
    day = (now + timedelta(days=1)).replace(hour=WORK_START, minute=0, second=0, microsecond=0)
    out: list[Slot] = []
    step = timedelta(minutes=duration_minutes)
    for _ in range(days_ahead):
        if day.weekday() < 5:
            t = day
            while t + step <= day.replace(hour=WORK_END) and len(out) < max_slots:
                if not any(t < e and s < t + step for s, e in busy):
                    out.append(Slot(start=t.isoformat(), end=(t + step).isoformat()))
                    t += timedelta(hours=2)  # spread offers through the day
                else:
                    t += step
        day += timedelta(days=1)
    res = Slots(timezone=str(TZ), slots=out[:max_slots])
    audit().append("workspace", c.user_id, "get_free_slots", {"days_ahead": days_ahead}, res.model_dump(),
                   True, c.trace_id)
    return res


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True,
                                      openWorldHint=False))
def create_interview_hold(
    start: Annotated[str, Field(description="ISO 8601 start time with offset")],
    title: Annotated[str, Field(min_length=3, max_length=200)],
    idempotency_key: Annotated[str, Field(min_length=8, max_length=128)],
    duration_minutes: Annotated[int, Field(ge=15, le=120)] = 30,
    ctx: Context | None = None,
) -> Hold:
    """Place a TENTATIVE calendar hold for a proposed interview slot (no invite is sent)."""
    c = caller_from_ctx(ctx)
    try:
        s = datetime.fromisoformat(start)
    except ValueError as e:
        raise ToolError(f"start must be ISO 8601: {e}") from e
    if s.tzinfo is None:
        s = s.replace(tzinfo=TZ)
    e = s + timedelta(minutes=duration_minutes)
    con = _db()
    prev = con.execute("select * from events where user_id=? and idempotency_key=?",
                       (c.user_id, idempotency_key)).fetchone()
    if prev:
        con.close()
        return Hold(event_id=prev["event_id"], start=prev["start"], end=prev["end"], title=prev["title"],
                    status=prev["status"], replayed=True)
    clash = con.execute("select 1 from events where user_id=? and status!='cancelled' and start<? and end>?",
                        (c.user_id, e.isoformat(), s.isoformat())).fetchone()
    if clash:
        con.close()
        raise ToolError("slot is no longer free")
    eid = "evt_" + uuid.uuid4().hex[:10]
    con.execute("insert into events values(?,?,?,?,?,?,?)",
                (eid, c.user_id, s.isoformat(), e.isoformat(), title, "tentative", idempotency_key))
    con.commit()
    con.close()
    res = Hold(event_id=eid, start=s.isoformat(), end=e.isoformat(), title=title, status="tentative")
    audit().append("workspace", c.user_id, "create_interview_hold", {"start": start, "title": title},
                   res.model_dump(), True, c.trace_id)
    return res


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True,
                                      openWorldHint=False))
def create_email_draft(
    to: Annotated[str, Field(min_length=3, max_length=200)],
    subject: Annotated[str, Field(min_length=1, max_length=200)],
    body: Annotated[str, Field(min_length=1, max_length=10000)],
    idempotency_key: Annotated[str, Field(min_length=8, max_length=128)],
    ctx: Context | None = None,
) -> EmailDraft:
    """Save an email DRAFT in the freelancer's mailbox. It is never sent by GigBid."""
    c = caller_from_ctx(ctx)
    con = _db()
    prev = con.execute("select * from drafts where user_id=? and idempotency_key=?",
                       (c.user_id, idempotency_key)).fetchone()
    if prev:
        con.close()
        return EmailDraft(draft_id=prev["draft_id"], to=prev["to_addr"], subject=prev["subject"], replayed=True)
    did = "drf_" + uuid.uuid4().hex[:10]
    con.execute("insert into drafts values(?,?,?,?,?,?,?)", (did, c.user_id, to, subject, body,
                                                             datetime.now(timezone.utc).isoformat(), idempotency_key))
    con.commit()
    con.close()
    res = EmailDraft(draft_id=did, to=to, subject=subject)
    audit().append("workspace", c.user_id, "create_email_draft", {"to": to, "subject": subject},
                   res.model_dump(), True, c.trace_id)
    return res


def http_app():
    from starlette.responses import JSONResponse
    app = mcp.streamable_http_app()

    async def health(request):
        return JSONResponse({"status": "ok", "server": "workspace"})
    app.add_route("/health", health)
    return BearerAuthMiddleware(app)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--http", action="store_true")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8093)
    a = ap.parse_args()
    if a.http:
        import uvicorn
        uvicorn.run(http_app(), host=a.host, port=a.port)
    else:
        mcp.run()


if __name__ == "__main__":
    main()
