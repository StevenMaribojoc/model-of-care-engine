#!/usr/bin/env bash
# Shared helpers for the demo scripts.

PORT="${PORT:-8000}"
PY=".venv/bin/python"
API="http://localhost:$PORT/api"

# Stop whatever is listening on PORT and wait until it is genuinely free.
#
# Waiting matters: the replacement binds the same port, and a stale listener
# that has not finished shutting down makes the new process die with
# "address already in use" while the old one keeps answering health checks --
# which looks exactly like the app ignoring your change.
stop_server() {
  local pids
  pids="$(lsof -ti:"$PORT" 2>/dev/null || true)"
  [ -n "$pids" ] && echo "$pids" | xargs kill 2>/dev/null || true

  for _ in $(seq 1 20); do
    lsof -ti:"$PORT" >/dev/null 2>&1 || return 0
    sleep 0.5
  done

  pids="$(lsof -ti:"$PORT" 2>/dev/null || true)"
  [ -n "$pids" ] && echo "$pids" | xargs kill -9 2>/dev/null || true
  sleep 1
}

# Start the API detached, with no inherited streams, so the calling script can
# exit (and its output can be piped) without waiting on the server.
start_server() {
  ( cd backend && nohup "../$PY" -m uvicorn app.main:app \
      --host 0.0.0.0 --port "$PORT" > /tmp/moc-demo.log 2>&1 < /dev/null & ) >/dev/null 2>&1

  for _ in $(seq 1 45); do
    curl -sf "$API/health" >/dev/null 2>&1 && return 0
    sleep 1
  done

  echo "server did not come up; last log lines:" >&2
  tail -5 /tmp/moc-demo.log >&2
  return 1
}

restart_server() { stop_server; start_server; }
