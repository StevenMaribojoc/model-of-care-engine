# Multi-stage build: compile the SPA with Node, then serve it and the API from a
# single Python image on one port.
#
# Verified: builds and runs, reports healthy, and serves the same results as the
# local path. Built natively for the development machine's architecture; add
# --platform linux/amd64 when building for a typical x86 cloud host.

# --- stage 1: frontend -------------------------------------------------------
FROM node:22-alpine AS frontend

WORKDIR /build
# Copy manifests first so the dependency layer is cached independently of source.
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ ./
# vite.config.ts writes to ../backend/app/static, so give it that path to land in.
RUN mkdir -p /backend/app && npm run build

# --- stage 2: runtime --------------------------------------------------------
FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY backend/requirements.txt ./backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

COPY backend/ ./backend/
COPY data/ ./data/
COPY --from=frontend /backend/app/static ./backend/app/static

# The SQLite database is rebuilt from data/*.csv on every startup, so the
# container holds no state and can be restarted or replaced freely.
# Most hosts (Render, Container Apps, Fly) inject the port to listen on rather
# than letting the image choose. Default to 8000 so local `docker run` is
# unchanged, but honour PORT when the platform sets it.
ENV PORT=8000
EXPOSE 8000
WORKDIR /app/backend

HEALTHCHECK --interval=30s --timeout=3s --start-period=20s \
  CMD python -c "import os,urllib.request;urllib.request.urlopen(f\"http://127.0.0.1:{os.environ.get('PORT','8000')}/api/health\")"

# Shell form so ${PORT} is expanded at container start, not frozen at build time.
CMD python -m uvicorn app.main:app --host 0.0.0.0 --port ${PORT}
