"""Tiny SQL helper: Postgres (psycopg) when DATABASE_URL is a postgres URL, else a local SQLite file.

Used for the app's own tables (users, jobs index, run events, memory observations). LangGraph
checkpoints and the store use their own Postgres classes (agent/persistence.py).
"""
from __future__ import annotations

import os
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from agent.settings import ROOT

_lock = threading.Lock()


def url() -> str:
    return os.getenv("DATABASE_URL", "")


def is_pg() -> bool:
    return url().startswith("postgres")


def _pg_url() -> str:
    return url().replace("postgresql+psycopg://", "postgresql://")


_pool = None


@contextmanager
def connect() -> Iterator[Any]:
    global _pool
    if is_pg():
        from psycopg_pool import ConnectionPool
        if _pool is None:
            _pool = ConnectionPool(_pg_url(), min_size=1, max_size=8, kwargs={"autocommit": True}, open=True)
        with _pool.connection() as con:
            yield con
    else:
        path = Path(os.getenv("GIGBID_SQLITE", ROOT / "data/processed/gigbid.sqlite3"))
        path.parent.mkdir(parents=True, exist_ok=True)
        with _lock:
            con = sqlite3.connect(path, timeout=30)
            try:
                yield con
                con.commit()
            finally:
                con.close()


def _sql(q: str) -> str:
    return q.replace("?", "%s") if is_pg() else q


def execute(q: str, params: tuple = ()) -> None:
    with connect() as con:
        con.execute(_sql(q), params)


def executescript(script: str) -> None:
    with connect() as con:
        for stmt in filter(None, (s.strip() for s in script.split(";"))):
            con.execute(stmt)


def query(q: str, params: tuple = ()) -> list[dict[str, Any]]:
    with connect() as con:
        cur = con.execute(_sql(q), params)
        cols = [d[0] for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()]
