# The deployed container (APP_ENV=deploy): the API, the built frontend, BLAST+
# and the pdbaa database in one image. Built for linux/arm64 to match the t4g
# instance; local checks can build for the host architecture.

# --- Frontend ----------------------------------------------------------------
# Node 22 matches CI; Vite 8 and Vitest 5 need 22.12 or later.
FROM node:22-slim AS frontend
WORKDIR /frontend
COPY purification-rescue-frontend/package.json purification-rescue-frontend/package-lock.json ./
RUN npm ci
COPY purification-rescue-frontend/ ./
# No VITE_API_URL: production builds call the API on their own origin.
RUN npm run build

# --- BLAST database ----------------------------------------------------------
FROM debian:bookworm-slim AS pdbaa
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /db/pdbaa
RUN curl -fsSLO https://ftp.ncbi.nlm.nih.gov/blast/db/pdbaa.tar.gz \
    && curl -fsSLO https://ftp.ncbi.nlm.nih.gov/blast/db/pdbaa.tar.gz.md5 \
    && md5sum -c pdbaa.tar.gz.md5 \
    && tar -xzf pdbaa.tar.gz \
    && rm pdbaa.tar.gz pdbaa.tar.gz.md5

# --- Runtime -----------------------------------------------------------------
FROM python:3.12-slim-bookworm
COPY --from=ghcr.io/astral-sh/uv:0.9.8 /uv /usr/local/bin/uv

RUN apt-get update \
    && apt-get install -y --no-install-recommends ncbi-blast+ \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev

COPY --from=pdbaa /db/pdbaa /db/pdbaa
COPY main.py schemas.py ./
COPY agent_engine/ agent_engine/
COPY --from=frontend /frontend/dist /app/frontend

ENV APP_ENV=deploy \
    BLAST_DB_PATH=/db/pdbaa/pdbaa \
    FRONTEND_DIST=/app/frontend \
    DATA_DIR=/data

# A fixed UID/GID, so the host can own the data volume mounted at /data to it.
# Until that mount exists, /data lives in the container's own layer.
RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid 10001 --no-create-home app \
    && mkdir /data \
    && chown app:app /data
USER app

EXPOSE 8000
# One worker: SQLite assumes a single writer process.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--proxy-headers", "--forwarded-allow-ips", "*"]
