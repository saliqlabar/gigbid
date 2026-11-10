#!/usr/bin/env bash
# Starts one service for the Playwright suite with the scripted test model and isolated state.
#   scripts/e2e_server.sh gigsim|api|api-degraded
set -euo pipefail
cd "$(dirname "$0")/.."
E2E=${E2E_DIR:-/tmp/gigbid-e2e}
export PYTHONPATH=. MODEL_PROFILE=test MCP_BACKEND=inproc WORKER_MODE=inline APP_SECRET_KEY=e2e-secret \
  GIGSIM_API_TOKENS=dev-token GIGSIM_API_TOKEN=dev-token GIGSIM_ERROR_RATE=0 GIGSIM_RATE_LIMIT_PER_MIN=100000 \
  REVIEWER_PASSWORD=reviewer-pass CORS_ORIGINS=http://localhost:8100
case "$1" in
  gigsim)
    rm -rf "$E2E" && mkdir -p "$E2E"
    GIGSIM_DB="$E2E/gigsim.sqlite3" exec .venv/bin/uvicorn gigsim.app:app --port 8181 --log-level warning ;;
  api)
    mkdir -p "$E2E/api"
    GIGSIM_BASE_URL=http://127.0.0.1:8181 GIGBID_STATE_DIR="$E2E/api" GIGBID_SQLITE="$E2E/api/app.sqlite3" \
      GIGBID_AUDIT_DB="$E2E/api/audit.sqlite3" GIGBID_IDEMPOTENCY_DB="$E2E/api/idem.sqlite3" \
      WORKSPACE_DB="$E2E/api/ws.sqlite3" MARKETPLACE_RATE_LIMIT=100000 \
      exec .venv/bin/uvicorn apps.api.main:app --port 8100 --log-level warning ;;
  api-degraded)
    mkdir -p "$E2E/deg"
    GIGSIM_BASE_URL=http://127.0.0.1:9 GIGBID_STATE_DIR="$E2E/deg" GIGBID_SQLITE="$E2E/deg/app.sqlite3" \
      GIGBID_AUDIT_DB="$E2E/deg/audit.sqlite3" GIGBID_IDEMPOTENCY_DB="$E2E/deg/idem.sqlite3" \
      exec .venv/bin/uvicorn apps.api.main:app --port 8101 --log-level warning ;;
esac
