# GigBid portfolio-mcp (build from repo root: docker build -f infra/docker/portfolio-mcp.Dockerfile .)
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app
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
RUN useradd -m gigbid && mkdir -p data/processed && chown -R gigbid /app/data
USER gigbid
EXPOSE 8092
CMD ["python", "-m", "mcp_servers.portfolio.server", "--http", "--port", "8092"]
