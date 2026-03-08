#!/usr/bin/env bash
# HomeAgent launcher — dev and production modes
#
# Usage:
#   ./start.sh           # development mode (Telegram long polling)
#   ./start.sh dev       # same as above
#   ./start.sh prod      # production mode via Docker Compose
#   ./start.sh logs      # tail Docker Compose logs
#   ./start.sh stop      # stop Docker Compose stack
#   ./start.sh restart   # rebuild and restart Docker Compose stack

set -euo pipefail

MODE="${1:-dev}"

# Detect docker compose invocation style (v2 plugin vs v1 standalone)
if docker compose version &>/dev/null 2>&1; then
  DC="docker compose"
elif command -v docker-compose &>/dev/null; then
  DC="docker-compose"
else
  DC="docker compose"  # fallback — will error with a clear message
fi

case "$MODE" in
  dev)
    echo "Starting HomeAgent in development mode (Telegram long polling)..."
    if ! command -v uv &>/dev/null; then
      echo "Error: 'uv' not found. Install it: https://docs.astral.sh/uv/"
      exit 1
    fi

    PROM_DIR="services/prometheus-mcp"
    PROM_PID=""

    # Start Prometheus MCP if the venv has been set up
    if [ -f "$PROM_DIR/.venv/bin/python" ]; then
      echo "Starting Prometheus MCP server..."
      (cd "$PROM_DIR" && PYTHONPATH=. .venv/bin/python app/main.py) &
      PROM_PID=$!
      echo "  Prometheus MCP running (PID $PROM_PID) → http://localhost:9000/mcp"
    fi

    TOOLS_DIR="services/tools-mcp"
    TOOLS_PID=""

    # Start Tools MCP if the venv has been set up
    if [ -f "$TOOLS_DIR/.venv/bin/python" ]; then
      echo "Starting Tools MCP server..."
      (cd "$TOOLS_DIR" && PYTHONPATH=. .venv/bin/python app/main.py) &
      TOOLS_PID=$!
      echo "  Tools MCP running (PID $TOOLS_PID) → http://localhost:9001/mcp"
    fi

    # Run app in background so we can track its PID and ensure it is killed on exit.
    uv run python -m app &
    APP_PID=$!

    # On Ctrl+C / SIGTERM / normal exit: stop both background processes, then wait.
    # If the app doesn't exit within 5 s after SIGTERM, send SIGKILL.
    trap '
      [ -n "$PROM_PID"  ] && kill -TERM "$PROM_PID"  2>/dev/null
      [ -n "$TOOLS_PID" ] && kill -TERM "$TOOLS_PID" 2>/dev/null
      [ -n "$APP_PID"   ] && kill -TERM "$APP_PID"   2>/dev/null
      sleep 5
      [ -n "$PROM_PID"  ] && kill -9 "$PROM_PID"  2>/dev/null
      [ -n "$TOOLS_PID" ] && kill -9 "$TOOLS_PID" 2>/dev/null
      [ -n "$APP_PID"   ] && kill -9 "$APP_PID"   2>/dev/null
      exit 0
    ' INT TERM EXIT

    wait $APP_PID
    ;;

  prod)
    echo "Starting HomeAgent in production mode (Docker Compose)..."
    if ! command -v docker &>/dev/null; then
      echo "Error: 'docker' not found. Install Docker Desktop."
      exit 1
    fi
    $DC build && $DC up -d
    echo ""
    echo "Stack is up. Follow logs with:  ./start.sh logs"
    echo "Stop with:                      ./start.sh stop"
    ;;

  logs)
    $DC logs -f
    ;;

  stop)
    echo "Stopping HomeAgent..."
    $DC down
    ;;

  restart)
    echo "Rebuilding and restarting HomeAgent..."
    $DC down
    $DC build && $DC up -d
    echo "Restarted. Follow logs with: ./start.sh logs"
    ;;

  *)
    echo "Usage: $0 [dev|prod|logs|stop|restart]"
    exit 1
    ;;
esac
