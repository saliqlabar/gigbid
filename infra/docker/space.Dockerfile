# GigBid in ONE container for a free public host (Hugging Face Docker Space: CPU, no GPU, port 7860).
# Same code as docker compose; differences: Redis runs in-process here, checkpoints/store use SQLite
# (agent/persistence.py) because free hosts give no managed Postgres, and models come from Groq's
# hosted open-weight models (MODEL_PROFILE=open_hosted) since there is no GPU.
# Build from repo root: docker build -f infra/docker/space.Dockerfile -t gigbid-space .
FROM node:20-slim AS web
WORKDIR /web
COPY apps/web/package.json apps/web/package-lock.json ./
RUN npm ci
COPY apps/web ./
RUN npx vite build

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app \
    MODEL_PROFILE=open_hosted MCP_BACKEND=http MARKETPLACE_MODE=gigsim \
    GIGSIM_BASE_URL=http://127.0.0.1:8081 REDIS_URL=redis://127.0.0.1:6379/0 \
    MARKETPLACE_MCP_URL=http://127.0.0.1:8091/mcp PORTFOLIO_MCP_URL=http://127.0.0.1:8092/mcp \
    WORKSPACE_MCP_URL=http://127.0.0.1:8093/mcp HF_HOME=/app/data/processed/hf
RUN apt-get update && apt-get install -y --no-install-recommends redis-server && rm -rf /var/lib/apt/lists/*
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
COPY infra/space/start.sh /app/start.sh
COPY --from=web /web/dist apps/web/dist
RUN useradd -m -u 1000 gigbid && mkdir -p data/processed && chown -R gigbid /app && chmod +x /app/start.sh
USER gigbid
# warm the CPU embedding + reranker models into the image so the first request is not a download
RUN python -c "from rag.index.embedder import embed; embed(['warm up'])" || true
EXPOSE 7860
CMD ["/app/start.sh"]
