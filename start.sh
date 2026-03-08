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

    # Run app in background so we can track its PID and ensure it is killed on exit.
    uv run python -m app &
    APP_PID=$!

    # On Ctrl+C / SIGTERM / normal exit: stop both background processes, then wait.
    # If the app doesn't exit within 5 s after SIGTERM, send SIGKILL.
    trap '
      [ -n "$PROM_PID" ] && kill -TERM "$PROM_PID" 2>/dev/null
      [ -n "$APP_PID"  ] && kill -TERM "$APP_PID"  2>/dev/null
      sleep 5
      [ -n "$PROM_PID" ] && kill -9 "$PROM_PID" 2>/dev/null
      [ -n "$APP_PID"  ] && kill -9 "$APP_PID"  2>/dev/null
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
    docker compose build && docker compose up -d
    echo ""
    echo "Stack is up. Follow logs with:  ./start.sh logs"
    echo "Stop with:                      ./start.sh stop"
    ;;

  logs)
    docker compose logs -f
    ;;

  stop)
    echo "Stopping HomeAgent..."
    docker compose down
    ;;

  restart)
    echo "Rebuilding and restarting HomeAgent..."
    docker compose down
    docker compose build && docker compose up -d
    echo "Restarted. Follow logs with: ./start.sh logs"
    ;;

  *)
    echo "Usage: $0 [dev|prod|logs|stop|restart]"
    exit 1
    ;;
esac
