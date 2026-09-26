# Multi-stage build: compile the SPA with Node, then serve it and the API from a
# single Python image on one port.
#
# NOTE: Docker was not available on the machine this was developed on, so this
# file is written from the working local setup but has NOT been built or run.
# The verified path is ./run.sh -- see the README. Treat this as the intended
# container shape rather than as a tested artifact.

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
EXPOSE 8000
WORKDIR /app/backend

HEALTHCHECK --interval=30s --timeout=3s --start-period=20s \
  CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/api/health')"

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
