"""arq worker: `arq worker.main.WorkerSettings` (needs REDIS_URL)."""
from __future__ import annotations

import os

from arq import cron
from arq.connections import RedisSettings

from agent.observability import setup_tracing
from agent.persistence import open_persistence
from agent.runner import new_conversation_graph, new_graph
from apps.api.auth.users import ensure_reviewer
from worker import tasks


async def startup(ctx):
    setup_tracing("gigbid-worker")
    ensure_reviewer()
    ctx["_persist"] = open_persistence()
    cp, store = await ctx["_persist"].__aenter__()
    ctx["store"] = store
    ctx["bid_graph"] = new_graph(cp, store)
    ctx["conv_graph"] = new_conversation_graph(cp, store)


async def shutdown(ctx):
    await ctx["_persist"].__aexit__(None, None, None)


class WorkerSettings:
    functions = [tasks.scout_user, tasks.analyse_job, tasks.handle_conversation,
                 tasks.continue_thread]
    cron_jobs = [cron(tasks.schedule, minute=set(range(60)), run_at_startup=True),
                 cron(tasks.poll_events, minute=set(range(0, 60, 2))),
                 cron(tasks.requeue_degraded, minute=set(range(1, 60, 3))),
                 cron(tasks.sim_clock, minute=set(range(0, 60, 5)))]
    on_startup, on_shutdown = startup, shutdown
    redis_settings = RedisSettings.from_dsn(os.getenv("REDIS_URL", "redis://localhost:6379/0"))
    max_jobs = int(os.getenv("WORKER_CONCURRENCY", "2"))       # local GPU: few concurrent LLM runs
    job_timeout = 1800
    max_tries = 3
    keep_result = 3600
