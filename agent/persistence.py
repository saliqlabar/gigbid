"""Durable checkpointer + long-term store (C6).

DATABASE_URL=postgresql://...  -> AsyncPostgresSaver + AsyncPostgresStore (docker compose, deploy)
otherwise                     -> AsyncSqliteSaver + AsyncSqliteStore in data/processed (local dev)
Both survive a process kill: a thread paused at an approval interrupt resumes from its last checkpoint.
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

from agent.settings import ROOT


def _pg() -> str | None:
    u = os.getenv("DATABASE_URL", "")
    return u.replace("postgresql+psycopg://", "postgresql://") if u.startswith("postgres") else None


@asynccontextmanager
async def open_persistence():
    """Yields (checkpointer, store)."""
    if (url := _pg()):
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
        from langgraph.store.postgres.aio import AsyncPostgresStore
        async with AsyncPostgresSaver.from_conn_string(url) as saver, \
                AsyncPostgresStore.from_conn_string(url) as store:
            await saver.setup()
            await store.setup()
            yield saver, store
    else:
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
        from langgraph.store.sqlite.aio import AsyncSqliteStore
        d = Path(os.getenv("GIGBID_STATE_DIR", ROOT / "data/processed"))
        d.mkdir(parents=True, exist_ok=True)
        async with AsyncSqliteSaver.from_conn_string(str(d / "checkpoints.sqlite3")) as saver, \
                AsyncSqliteStore.from_conn_string(str(d / "store.sqlite3")) as store:
            await store.setup()
            yield saver, store
