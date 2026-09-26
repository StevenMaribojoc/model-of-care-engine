#!/usr/bin/env bash
#
# One command to run the whole thing locally: creates a virtualenv, installs
# dependencies, builds the frontend, and serves the API and the SPA together on
# http://localhost:8000.
#
# Re-running is cheap: each step is skipped if its output is already current.
#
#   ./run.sh              build if needed, then serve
#   ./run.sh --rebuild    force a fresh frontend build
#   ./run.sh --api-only   skip the frontend entirely (no Node required)
#
set -euo pipefail

cd "$(dirname "$0")"
PORT="${PORT:-8000}"
VENV=".venv"
REBUILD=0
API_ONLY=0

for arg in "$@"; do
  case "$arg" in
    --rebuild)  REBUILD=1 ;;
    --api-only) API_ONLY=1 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

say() { printf '\033[1;34m==>\033[0m %s\n' "$1"; }
die() { printf '\033[1;31merror:\033[0m %s\n' "$1" >&2; exit 1; }

# --- Python ------------------------------------------------------------------

find_python() {
  for candidate in python3.12 python3.11 python3.13 python3; do
    if command -v "$candidate" >/dev/null 2>&1; then
      # 3.10+ is required: the code uses `X | None` type syntax throughout.
      if "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)'; then
        echo "$candidate"; return 0
      fi
    fi
  done
  return 1
}

if [ ! -d "$VENV" ]; then
  PYTHON="$(find_python)" || die "Python 3.10+ not found. On macOS: brew install python@3.12"
  say "Creating virtualenv with $($PYTHON --version)"
  "$PYTHON" -m venv "$VENV"
fi

say "Installing Python dependencies"
"$VENV/bin/pip" install --quiet --upgrade pip
"$VENV/bin/pip" install --quiet -r backend/requirements.txt

# --- Frontend ----------------------------------------------------------------

STATIC_DIR="backend/app/static"

if [ "$API_ONLY" -eq 1 ]; then
  say "Skipping frontend (--api-only)"
elif ! command -v npm >/dev/null 2>&1; then
  echo "note: npm not found, serving the API only. Install Node 18+ for the UI." >&2
elif [ "$REBUILD" -eq 1 ] || [ ! -f "$STATIC_DIR/index.html" ]; then
  say "Building frontend"
  ( cd frontend && npm install --silent && npm run build )
else
  say "Frontend already built (use --rebuild to force)"
fi

# --- Serve -------------------------------------------------------------------

say "Starting on http://localhost:$PORT"
echo "    UI       http://localhost:$PORT"
echo "    API docs http://localhost:$PORT/docs"
echo
# The database is rebuilt from data/*.csv at startup, so there is nothing to
# migrate and no state to clear between runs.
cd backend
exec "../$VENV/bin/python" -m uvicorn app.main:app --host 0.0.0.0 --port "$PORT"
