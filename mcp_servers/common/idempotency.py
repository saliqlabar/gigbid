"""Server-side idempotency store for write tools.

Key = (user, tool, idempotency_key). A retried call with the same key and the same arguments returns
the stored result without touching the marketplace again; the same key with different arguments is
rejected. This sits in front of both adapters, so live Upwork (which has no idempotency keys) is also
protected from double drafts.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[2]


class IdempotencyConflict(Exception):
    pass


class IdempotencyStore:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path or os.getenv("GIGBID_IDEMPOTENCY_DB", _ROOT / "data/processed/idempotency.sqlite3"))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        with sqlite3.connect(self.path) as con:
            con.execute("create table if not exists idem(user_id text, tool text, key text, args_hash text,"
                        " result text, primary key(user_id, tool, key))")

    @staticmethod
    def hash_args(args: dict[str, Any]) -> str:
        return hashlib.sha256(json.dumps(args, sort_keys=True, default=str).encode()).hexdigest()

    def get(self, user_id: str, tool: str, key: str, args: dict[str, Any]) -> dict[str, Any] | None:
        with sqlite3.connect(self.path) as con:
            r = con.execute("select args_hash, result from idem where user_id=? and tool=? and key=?",
                            (user_id, tool, key)).fetchone()
        if not r:
            return None
        if r[0] != self.hash_args(args):
            raise IdempotencyConflict(f"idempotency_key {key!r} was already used with different arguments")
        return json.loads(r[1])

    def put(self, user_id: str, tool: str, key: str, args: dict[str, Any], result: dict[str, Any]) -> None:
        with sqlite3.connect(self.path) as con:
            con.execute("insert or ignore into idem values(?,?,?,?,?)",
                        (user_id, tool, key, self.hash_args(args), json.dumps(result, default=str)))
