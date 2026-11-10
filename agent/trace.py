"""Execution trace store (C2): every node start/end, tool call (name, arguments, result) and error.

Kept OUT of graph state. The worker writes events while the graph streams; the API streams them to
the run viewer over SSE. Everything is PII-masked before it is written. OpenTelemetry spans go to
Phoenix separately (agent/observability.py) with the same trace id.
"""
from __future__ import annotations

import json
import time
from contextvars import ContextVar
from typing import Any

from agent import db
from agent.safety.pii import mask

_SCHEMA = """
create table if not exists run_events(
  id text primary key, thread_id text, ts double precision, node text, kind text, data text, trace_id text);
create index if not exists run_events_thread on run_events(thread_id, ts)
"""
_ready = False
current_thread: ContextVar[tuple[str, str] | None] = ContextVar("gigbid_thread", default=None)


def _init() -> None:
    global _ready
    if not _ready:
        db.executescript(_SCHEMA.replace("double precision", "real") if not db.is_pg() else _SCHEMA)
        _ready = True


def emit(node: str, kind: str, data: dict[str, Any] | None = None, thread_id: str | None = None,
         trace_id: str | None = None) -> None:
    cur = current_thread.get()
    thread_id = thread_id or (cur[0] if cur else "unknown")
    trace_id = trace_id or (cur[1] if cur else None)
    _init()
    import uuid
    db.execute("insert into run_events(id, thread_id, ts, node, kind, data, trace_id) values(?,?,?,?,?,?,?)",
               (uuid.uuid4().hex, thread_id, time.time(), node, kind,
                json.dumps(mask(data or {}), default=str)[:20000], trace_id))


def events(thread_id: str, after_ts: float = 0.0) -> list[dict[str, Any]]:
    _init()
    rows = db.query("select * from run_events where thread_id=? and ts>? order by ts", (thread_id, after_ts))
    for r in rows:
        r["data"] = json.loads(r["data"] or "{}")
    return rows
