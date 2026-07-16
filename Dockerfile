# ── Stage 1: build the dashboard (Next.js static export) ────────────
FROM node:22-alpine AS frontend
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci
COPY frontend/ .
RUN NEXT_TELEMETRY_DISABLED=1 npm run build

# ── Stage 2: API server (serves the dashboard from /frontend/out) ───
FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml README.md ./
COPY server/ server/
RUN pip install --no-cache-dir . \
    && useradd --system --uid 10001 --create-home stackmemory \
    && mkdir -p /data \
    && chown -R stackmemory:stackmemory /app /data

COPY --from=frontend /frontend/out frontend/out
RUN chown -R stackmemory:stackmemory /app/frontend

ENV SQLITE_DB_PATH=/data/stackmemory.db \
    EMBEDDER_MODE=hash \
    NEXT_TELEMETRY_DISABLED=1

USER stackmemory
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3)" || exit 1
CMD ["uvicorn", "server.api:app", "--host", "0.0.0.0", "--port", "8000"]
