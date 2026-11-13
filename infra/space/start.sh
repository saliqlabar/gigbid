#!/bin/bash
# Starts every GigBid service inside the single deploy container; the API (with the built web app) is the
# public port. If any service dies the container exits, so the host restarts it.
set -e
cd /app
redis-server --port 6379 --save "" --daemonize yes
uvicorn gigsim.app:app --host 127.0.0.1 --port 8081 &
python -m mcp_servers.marketplace.server --http --port 8091 &
python -m mcp_servers.portfolio.server --http --port 8092 &
python -m mcp_servers.workspace.server --http --port 8093 &
arq worker.main.WorkerSettings &
uvicorn apps.api.main:app --host 0.0.0.0 --port "${PORT:-7860}" &
wait -n
exit 1
