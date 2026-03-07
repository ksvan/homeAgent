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

    # Kill background services on exit (Ctrl+C or normal exit)
    trap '[ -n "$PROM_PID" ] && kill "$PROM_PID" 2>/dev/null; exit 0' INT TERM EXIT

    uv run python -m app
    ;;

  prod)
    echo "Starting HomeAgent in production mode (Docker Compose)..."
    if ! command -v docker &>/dev/null; then
      echo "Error: 'docker' not found. Install Docker Desktop."
      exit 1
    fi
    docker compose up --build -d
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
    docker compose up --build -d
    echo "Restarted. Follow logs with: ./start.sh logs"
    ;;

  *)
    echo "Usage: $0 [dev|prod|logs|stop|restart]"
    exit 1
    ;;
esac
