# GigBid as ONE process for Render's free tier (512 MB RAM, no GPU). Same code as docker compose, switched by env:
#   WORKER_MODE=inprocess   background tasks + cron clock inside the API (apps/api/inprocess_worker.py), no Redis
#   GIGSIM_INPROCESS=1      GigSim mounted in-process (ASGI transport) instead of its own container
#   MCP_BACKEND=inproc      MCP servers called in-process (same FastMCP servers, same schemas and audit log)
#   MODEL_PROFILE=open_hosted  open-weight models on Groq (no GPU on free hosts)
#   EMBEDDING_MODEL=BAAI/bge-small-en-v1.5  nomic-embed-text-v1.5 needs ~900 MB RAM alone; the cost of this swap
#                           is measured in evals/results/rag_variants.json (deploy row)
# Checkpoints and long-term memory use SQLite (agent/persistence.py) since the free tier has no Postgres.
# Build from repo root: docker build -f infra/docker/render.Dockerfile -t gigbid-render .
FROM node:20-slim AS web
WORKDIR /web
COPY apps/web/package.json apps/web/package-lock.json ./
RUN npm ci
COPY apps/web ./
RUN npx vite build

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app \
    MODEL_PROFILE=open_hosted MCP_BACKEND=inproc WORKER_MODE=inprocess GIGSIM_INPROCESS=1 MARKETPLACE_MODE=gigsim \
    EMBEDDING_MODEL=BAAI/bge-small-en-v1.5 OMP_NUM_THREADS=1 GIGSIM_DB=/app/data/processed/gigsim.sqlite3
WORKDIR /app
COPY requirements.lock .
RUN pip install --no-cache-dir -r requirements.lock
COPY agent agent
COPY apps/api apps/api
COPY mcp_servers mcp_servers
COPY rag rag
COPY gigsim gigsim
COPY worker worker
COPY data/personas data/personas
COPY data/policy_pack data/policy_pack
COPY --from=web /web/dist apps/web/dist
RUN useradd -m -u 1000 gigbid && mkdir -p data/processed && chown -R gigbid /app
USER gigbid
# Download the embedding model and embed every persona corpus at BUILD time, so the running server only
# embeds queries (keeps memory and first-request latency down).
COPY scripts/warm_index.py scripts/warm_index.py
RUN python scripts/warm_index.py
EXPOSE 10000
CMD ["sh", "-c", "uvicorn apps.api.main:app --host 0.0.0.0 --port ${PORT:-10000} --workers 1"]
