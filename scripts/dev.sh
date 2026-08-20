#!/usr/bin/env bash

# Run the API and Vite directly on the host for fast edit/reload cycles.
# Usage: ./scripts/dev.sh
# Optional: API_PORT=8001 WEB_PORT=5174 ./scripts/dev.sh

set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
API_DIR="$ROOT_DIR/api"
WEB_DIR="$ROOT_DIR/web"
API_PYTHON="$API_DIR/.venv/bin/python"
VITE_BIN="$WEB_DIR/node_modules/.bin/vite"
API_PORT="${API_PORT:-8000}"
WEB_PORT="${WEB_PORT:-5173}"

if [[ -f "$ROOT_DIR/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$ROOT_DIR/.env"
  set +a
fi

if [[ ! -x "$API_PYTHON" ]]; then
  echo "Missing API virtualenv at $API_DIR/.venv" >&2
  echo "Create it with: python3 -m venv $API_DIR/.venv && $API_DIR/.venv/bin/pip install -r $API_DIR/requirements.txt" >&2
  exit 1
fi

if [[ ! -x "$VITE_BIN" ]]; then
  echo "Missing web dependencies at $WEB_DIR/node_modules" >&2
  echo "Install them with: cd $WEB_DIR && npm install" >&2
  exit 1
fi

API_PID=""
WEB_PID=""

cleanup() {
  local exit_code=$?
  trap - EXIT INT TERM
  [[ -n "$API_PID" ]] && kill "$API_PID" 2>/dev/null || true
  [[ -n "$WEB_PID" ]] && kill "$WEB_PID" 2>/dev/null || true
  [[ -n "$API_PID" ]] && wait "$API_PID" 2>/dev/null || true
  [[ -n "$WEB_PID" ]] && wait "$WEB_PID" 2>/dev/null || true
  exit "$exit_code"
}

trap cleanup EXIT INT TERM

echo "Preparing the development database…"
(
  cd "$API_DIR"
  "$API_DIR/.venv/bin/alembic" upgrade head
)

echo "Starting API at http://localhost:$API_PORT (reload enabled)"
(
  cd "$API_DIR"
  exec "$API_PYTHON" -m uvicorn app.main:app --reload --host 127.0.0.1 --port "$API_PORT"
) &
API_PID=$!

echo "Starting web app at http://localhost:$WEB_PORT (Vite HMR enabled)"
(
  cd "$WEB_DIR"
  VITE_API_URL="${VITE_API_URL:-http://localhost:$API_PORT}" \
    exec "$VITE_BIN" --host 0.0.0.0 --port "$WEB_PORT" --clearScreen false
) &
WEB_PID=$!

echo ""
echo "Development app is running. Press Ctrl-C to stop both services."
echo "  Web: http://localhost:$WEB_PORT"
echo "  API: http://localhost:$API_PORT/docs"

while kill -0 "$API_PID" 2>/dev/null && kill -0 "$WEB_PID" 2>/dev/null; do
  sleep 1
done

echo "A development process stopped; shutting down the other service." >&2
exit 1
