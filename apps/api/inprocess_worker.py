"""In-process worker for the single-process deploy (WORKER_MODE=inprocess; a 512 MB free host cannot also run
Redis and a separate arq worker). Same task functions and the same idempotency as the arq worker
(worker/main.py): queued work runs as background asyncio tasks keyed by job id (a key already running is
not started twice), and a small clock runs the same cron jobs at the same cadences. Web requests never wait
for a graph run.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time

log = logging.getLogger("gigbid.inprocess_worker")
_running: dict[str, asyncio.Task] = {}
_sem: asyncio.Semaphore | None = None

CRON = (("schedule", 60), ("poll_events", 120), ("requeue_degraded", 180), ("sim_clock", 300))


def _ctx(state) -> dict:
    return {"inline": True, "app_state": state}


async def _run(state, fn: str, *args) -> None:
    from worker import tasks
    global _sem
    # one graph at a time by default: measured peak RSS was 406 MB with two concurrent runs on a 512 MB host
    _sem = _sem or asyncio.Semaphore(int(os.getenv("WORKER_CONCURRENCY", "1")))
    async with _sem:
        try:
            await getattr(tasks, fn)(_ctx(state), *args)
        except Exception:  # noqa: BLE001 - one failed task must not stop the worker; graphs record their own errors
            log.exception("in-process task %s failed", fn)


def submit(state, fn: str, *args, job_id: str | None = None) -> dict:
    key = job_id or f"{fn}:{args}"
    t = _running.get(key)
    if t is not None and not t.done():
        return {"queued": False, "job_id": key, "reason": "already running"}
    task = asyncio.create_task(_run(state, fn, *args))
    _running[key] = task
    task.add_done_callback(lambda _t: _running.pop(key, None))
    return {"queued": True, "job_id": key}


def start(state) -> asyncio.Task:
    async def clock():
        last = {name: 0.0 for name, _ in CRON}
        while True:
            now = time.time()
            for name, every in CRON:
                if now - last[name] >= every:
                    last[name] = now
                    submit(state, name, job_id=f"cron:{name}")
            await asyncio.sleep(10)
    return asyncio.create_task(clock())
