"""Append-only audit log: user, tool, arguments, result, trace id (A2).

SQLite triggers reject UPDATE and DELETE, so rows can only ever be appended. Arguments and results
are PII-masked before they are written.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent.safety.pii import mask

_ROOT = Path(__file__).resolve().parents[2]


class AuditLog:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path or os.getenv("GIGBID_AUDIT_DB", _ROOT / "data/processed/audit.sqlite3"))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with sqlite3.connect(self.path) as con:
            con.executescript("""
            create table if not exists audit(seq integer primary key autoincrement, ts text, server text,
              user_id text, tool text, arguments text, result text, ok integer, trace_id text);
            create trigger if not exists audit_no_update before update on audit
              begin select raise(abort, 'audit log is append-only'); end;
            create trigger if not exists audit_no_delete before delete on audit
              begin select raise(abort, 'audit log is append-only'); end;
            """)

    def append(self, server: str, user_id: str, tool: str, arguments: dict[str, Any], result: Any,
               ok: bool, trace_id: str | None) -> None:
        row = (datetime.now(timezone.utc).isoformat(), server, user_id, tool,
               json.dumps(mask(arguments), default=str), json.dumps(mask(result), default=str)[:20000],
               int(ok), trace_id)
        with self._lock, sqlite3.connect(self.path) as con:
            con.execute("insert into audit(ts,server,user_id,tool,arguments,result,ok,trace_id)"
                        " values(?,?,?,?,?,?,?,?)", row)

    def tail(self, n: int = 50, user_id: str | None = None) -> list[dict[str, Any]]:
        with sqlite3.connect(self.path) as con:
            con.row_factory = sqlite3.Row
            q = "select * from audit" + (" where user_id=?" if user_id else "") + " order by seq desc limit ?"
            rows = con.execute(q, (user_id, n) if user_id else (n,)).fetchall()
        return [dict(r) for r in rows]
