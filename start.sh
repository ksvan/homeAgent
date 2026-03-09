#!/usr/bin/env bash
# HomeAgent launcher
#
# Usage:
#   ./start.sh           # build and start (docker compose up --build -d)
#   ./start.sh logs      # tail Docker Compose logs
#   ./start.sh stop      # stop Docker Compose stack
#   ./start.sh restart   # rebuild and restart Docker Compose stack

set -euo pipefail

MODE="${1:-up}"

# Detect docker compose invocation style (v2 plugin vs v1 standalone)
if docker compose version &>/dev/null 2>&1; then
  DC="docker compose"
elif command -v docker-compose &>/dev/null; then
  DC="docker-compose"
else
  DC="docker compose"  # fallback — will error with a clear message
fi

case "$MODE" in
  up)
    echo "Starting HomeAgent..."
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
    echo "Usage: $0 [up|logs|stop|restart]"
    exit 1
    ;;
esac
