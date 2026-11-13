# Runbook

## Start

| Goal | Command |
|---|---|
| Everything, local, open models | `cp .env.example .env` (fill it), then `docker compose -f infra/docker-compose.yml --env-file .env up --build` → web on http://localhost:8080, API :8000, Phoenix :6006 |
| Dev loop (no Docker) | `make dev` (GigSim :8081, API :8000 with in-process MCP, Vite dev server) |
| Tests | `make test` (unit, integration, contract; scripted model) · `make e2e` (Playwright) · `make lint` |
| Evaluations | `make eval` (retrieval, decisions, fixed GigSim reply-rate run, results table) · `make smoke` (30-job CI gate) |
| Deploy image | `docker build -f infra/docker/space.Dockerfile .` (one container; see "Deployment") |

Secrets come only from environment variables (`.env`, never committed; CI checks). Required: `APP_SECRET_KEY`,
`REVIEWER_PASSWORD`, `MCP_TOKENS` / `MCP_CLIENT_TOKEN` (bearer tokens for the MCP servers), `TOKEN_ENCRYPTION_KEY` (Fernet; encrypts OAuth
tokens), `GROQ_API_KEY` for the hosted profile. Every variable is listed in `.env.example`.

## Schedules and rate limits

* Worker (`worker/main.py`, arq on Redis): `schedule` runs every minute and queues a scout for each saved
  search whose own interval has elapsed; `poll_events` every 2 min (GigSim events → outcomes, client messages);
  `requeue_degraded` every 3 min.
* Saved-search interval: default 30 min, minimum **15 min in GigSim** and **60 min in live mode**
  (`worker/tasks.py`). Why 60 min live: a freelancer checking new jobs a few times a day is normal use; job
  posts stay open for days, so polling faster than hourly gains almost nothing and starts to look like
  scraping. Each scout is one `find_jobs search` call per saved search (≤ 10 results), and job details are
  fetched only for new ids.
* Marketplace MCP server: per-user token bucket at 20 requests/min (`marketplace.max_requests_per_minute`),
  below GigSim's 60/min; GigSim 429s and 5xx are retried with backoff (bounded) in the adapter.
* Idempotency: a job thread id is `bid-<user>-<job>`, so a re-queued or duplicated job is never analysed
  twice; every write carries an idempotency key, so a retried submit never spends Connects twice.

## Degraded mode (viva question 1: "kill the marketplace MCP server")

1. Stop it (`docker compose stop marketplace-mcp`, or kill the process).
2. Any node that needs it gets `MarketplaceDown` after bounded retries (`agent/mcp_client.py`), the node
   wrapper (`agent/nodes/_base.py`) sets the thread to `degraded`, and the thread stops cleanly.
3. The UI polls `/api/status`; a red banner says the marketplace is unavailable and how many jobs are queued
   (`apps/web/src/components/StatusBanner.tsx`, Playwright test `degraded-banner`).
4. Start it again. `requeue_degraded` sees the server healthy and continues each degraded thread from its
   last checkpoint; nothing is re-analysed or re-submitted.

## Crash and resume (viva question 2)

1. Open a job that is waiting for approval in the Proposal studio.
2. Kill the API or worker (`docker compose kill api`), restart it (`docker compose start api`).
3. Approve in the UI. The approval resumes the checkpointed thread (Postgres checkpointer) and the submitter
   uses the stored idempotency keys, so GigSim shows exactly one proposal and one Connects charge.
   Automated version: `tests/integration/test_bid_graph.py::test_crash_and_resume_submits_exactly_once`.
4. Replay any job from any node: Agent run viewer → checkpoint history → "replay from here"
   (`POST /api/runs/{thread_id}/replay/{checkpoint_id}`).

## Live mode (Upwork)

* Connect once: Settings → Connect Upwork (OAuth 2.1 via the official MCP server), or
  `python scripts/upwork_connect.py --user <user_id>`. Tokens are stored Fernet-encrypted per user
  (`apps/api/auth/token_store.py`) and never logged.
* Switch: `marketplace.mode: upwork_live` in `agent/config.yaml` (or `MARKETPLACE_MODE=upwork_live`). Only
  the adapter changes (`mcp_servers/marketplace/adapters/upwork_live.py`); graph, prompts and tool schemas do not.
* GigBid never submits or sends on Upwork: an approved proposal becomes an Upwork *preview* only (Upwork's
  `manage_proposals create`); `confirm_preview` and Upwork's `send_message` are deliberately not mapped. The
  user submits on Upwork.
* The deployed reviewer instance runs GigSim only; live mode is for the author's own account.

## Deployment

The public instance is one container (`infra/docker/space.Dockerfile`, start script `infra/space/start.sh`):
GigSim, the three MCP servers over HTTP with bearer tokens, the arq worker with an in-container Redis and the
API serving the built web app. Differences from `docker compose`, and why: SQLite checkpointer and store
instead of Postgres (free hosts have no managed Postgres; `agent/persistence.py` picks by `DATABASE_URL`),
and the `open_hosted` model profile (Groq open-weight models) because free hosts have no GPU. The reviewer
login is created at start-up from `REVIEWER_PASSWORD` with the full-stack persona preloaded.

## Recovery checklist

| Symptom | Where to look | Fix |
|---|---|---|
| Thread in `dead_letter` | Job detail → errors; run viewer trace | fix cause, press Retry (re-runs from the failed node) |
| Thread in `needs_attention` | step/token budget exceeded (`budgets` in config) | raise budget or edit draft manually |
| Many 429s from Groq | Phoenix spans, API logs | free-tier limit; cheap nodes fall back to the fallback model |
| Ollama connection errors | `ollama ps`, WSL RAM | run GPU evals one at a time; restart Ollama |
