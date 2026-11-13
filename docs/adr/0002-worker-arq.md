# 0002 Background work in arq on Redis

**Status:** accepted · **Context:** E1.

**Decision.** Scouting and long graph runs run in an arq worker (`worker/`), not in web requests. arq is
async-native like FastAPI and LangGraph, so the worker reuses the same `async` graph code with no thread
bridging; Celery would need a sync wrapper around every graph call.

**Consequences.** Each saved search has its own interval (default 30 min, never below 15 min in GigSim or 60 min in live
mode; justified in
`docs/RUNBOOK.md`). Work is idempotent: job threads are keyed `bid-<user>-<job>`, so a restarted worker never
analyses a job twice, and submission uses idempotency keys, so it never submits twice.
