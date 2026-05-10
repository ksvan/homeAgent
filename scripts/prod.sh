#!/usr/bin/env bash
#
# HomeAgent production deployment helper.
#
# Required env:
#   HOMEAGENT_DEPLOY_HOST       Mac mini hostname or IP
#
# Optional env:
#   HOMEAGENT_DEPLOY_USER       SSH user, defaults to current local user
#   HOMEAGENT_DEPLOY_PATH       Remote app path, defaults to /Users/<user>/homeAgent
#   HOMEAGENT_DEPLOY_TARGET     Full SSH target override, e.g. macmini.local
#   HOMEAGENT_SSH_KEY           SSH private key path, defaults to ~/.ssh/id_ed25519_homeagent when present
#   HOMEAGENT_SSH_OPTS          Extra ssh options, e.g. "-p 2222"
#   HOMEAGENT_SSH_CONNECT_TIMEOUT SSH connect timeout seconds, defaults to 10
#   HOMEAGENT_SSH_ALIVE_INTERVAL SSH keepalive interval seconds, defaults to 60
#   HOMEAGENT_SSH_ALIVE_COUNT    SSH keepalive failures before disconnect, defaults to 10
#   HOMEAGENT_HEALTH_TIMEOUT      Health check timeout seconds, defaults to 15
#   HOMEAGENT_RSYNC_OPTS        Extra rsync options, e.g. "--progress"
#
# Usage:
#   HOMEAGENT_DEPLOY_HOST=macmini.local ./scripts/prod.sh bootstrap
#   HOMEAGENT_DEPLOY_HOST=macmini.local ./scripts/prod.sh migrate
#   HOMEAGENT_DEPLOY_HOST=macmini.local ./scripts/prod.sh deploy
#   HOMEAGENT_DEPLOY_HOST=macmini.local ./scripts/prod.sh logs

set -euo pipefail

MODE="${1:-help}"
LOCAL_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

DEPLOY_HOST="${HOMEAGENT_DEPLOY_HOST:-}"
DEPLOY_USER="${HOMEAGENT_DEPLOY_USER:-$(id -un)}"
DEPLOY_TARGET="${HOMEAGENT_DEPLOY_TARGET:-}"
DEPLOY_PATH="${HOMEAGENT_DEPLOY_PATH:-/Users/${DEPLOY_USER}/homeAgent}"
DEFAULT_SSH_KEY="$HOME/.ssh/id_ed25519_homeagent"
SSH_CONNECT_TIMEOUT="${HOMEAGENT_SSH_CONNECT_TIMEOUT:-10}"
SSH_ALIVE_INTERVAL="${HOMEAGENT_SSH_ALIVE_INTERVAL:-60}"
SSH_ALIVE_COUNT="${HOMEAGENT_SSH_ALIVE_COUNT:-10}"
HEALTH_TIMEOUT="${HOMEAGENT_HEALTH_TIMEOUT:-15}"
if [[ -n "${HOMEAGENT_SSH_KEY:-}" ]]; then
  SSH_KEY="$HOMEAGENT_SSH_KEY"
elif [[ -f "$DEFAULT_SSH_KEY" ]]; then
  SSH_KEY="$DEFAULT_SSH_KEY"
else
  SSH_KEY=""
fi

if [[ -z "$DEPLOY_TARGET" && -n "$DEPLOY_HOST" ]]; then
  DEPLOY_TARGET="${DEPLOY_USER}@${DEPLOY_HOST}"
fi

usage() {
  cat <<'EOF'
Usage:
  HOMEAGENT_DEPLOY_HOST=<host> ./scripts/prod.sh <command>

Commands:
  bootstrap   Create the remote directory and verify Docker/Compose over SSH
  deploy      Sync code/prompts/services only, then rebuild and restart remotely
  migrate     One-time consistent migration of code, .env, prompts, and data
  up          Start the remote Compose stack
  down        Stop the remote Compose stack
  restart     Rebuild and restart the remote Compose stack
  status      Show remote Compose status and health endpoint result
  logs        Tail remote Compose logs
  logs-tail   Print the last 200 remote Compose log lines and exit
  backup      Run the remote backup script
  install-key Install this machine's public deploy key on the remote host
  sync-env    Copy local .env to the remote host without syncing data

Environment:
  HOMEAGENT_DEPLOY_HOST=<host-or-ip>        required unless HOMEAGENT_DEPLOY_TARGET is set
  HOMEAGENT_DEPLOY_USER=<ssh-user>          defaults to current user
  HOMEAGENT_DEPLOY_PATH=<remote-path>       defaults to /Users/<user>/homeAgent
  HOMEAGENT_DEPLOY_TARGET=<ssh-target>      overrides user@host
  HOMEAGENT_SSH_KEY=~/.ssh/key              defaults to ~/.ssh/id_ed25519_homeagent if present
  HOMEAGENT_SSH_OPTS="-p 2222"             optional extra ssh options
  HOMEAGENT_SSH_CONNECT_TIMEOUT=10         SSH connect timeout seconds
  HOMEAGENT_SSH_ALIVE_INTERVAL=60          SSH keepalive interval seconds
  HOMEAGENT_SSH_ALIVE_COUNT=10             SSH keepalive failures before disconnect
  HOMEAGENT_HEALTH_TIMEOUT=15              Remote health check timeout seconds
  HOMEAGENT_RSYNC_OPTS="--progress"        optional extra rsync options
EOF
}

step() {
  printf '\n[%s] %s\n' "$(date '+%H:%M:%S')" "$*"
}

require_target() {
  if [[ -z "$DEPLOY_TARGET" ]]; then
    echo "HOMEAGENT_DEPLOY_HOST or HOMEAGENT_DEPLOY_TARGET is required." >&2
    usage >&2
    exit 2
  fi
}

require_file() {
  local path="$1"
  if [[ ! -e "$LOCAL_ROOT/$path" ]]; then
    echo "Required local path missing: $path" >&2
    exit 1
  fi
}

remote_bash() {
  local script="$1"
  local prelude='export PATH="/opt/homebrew/bin:/usr/local/bin:/Applications/Docker.app/Contents/Resources/bin:$HOME/.docker/bin:$HOME/.orbstack/bin:$PATH"'
  local ssh_args=()
  ssh_args+=("-o" "ConnectTimeout=${SSH_CONNECT_TIMEOUT}")
  ssh_args+=("-o" "ServerAliveInterval=${SSH_ALIVE_INTERVAL}")
  ssh_args+=("-o" "ServerAliveCountMax=${SSH_ALIVE_COUNT}")
  if [[ -n "$SSH_KEY" ]]; then
    ssh_args+=("-i" "$SSH_KEY")
  fi
  if [[ -n "${HOMEAGENT_SSH_OPTS:-}" ]]; then
    # shellcheck disable=SC2206
    ssh_args+=(${HOMEAGENT_SSH_OPTS})
  fi
  # HOMEAGENT_SSH_OPTS is intentionally word-split to support normal ssh flags.
  ssh ${ssh_args[@]+"${ssh_args[@]}"} "$DEPLOY_TARGET" "bash -lc $(printf '%q' "$prelude"$'\n'"$script")"
}

rsync_remote_shell() {
  local shell_cmd="ssh -o ConnectTimeout=${SSH_CONNECT_TIMEOUT} -o ServerAliveInterval=${SSH_ALIVE_INTERVAL} -o ServerAliveCountMax=${SSH_ALIVE_COUNT}"
  if [[ -n "$SSH_KEY" ]]; then
    shell_cmd+=" -i $(printf '%q' "$SSH_KEY")"
  fi
  if [[ -n "${HOMEAGENT_SSH_OPTS:-}" ]]; then
    shell_cmd+=" ${HOMEAGENT_SSH_OPTS}"
  fi
  printf '%s' "$shell_cmd"
}

remote_compose() {
  local args="$*"
  step "Remote docker compose $args"
  remote_bash "
    set -euo pipefail
    cd $(printf '%q' "$DEPLOY_PATH")
    if docker compose version >/dev/null 2>&1; then
      docker compose $args
    elif command -v docker-compose >/dev/null 2>&1; then
      docker-compose $args
    else
      echo 'Docker Compose is not installed on the remote host.' >&2
      exit 1
    fi
  "
}

rsync_base() {
  local remote_shell
  remote_shell="$(rsync_remote_shell)"
  # HOMEAGENT_RSYNC_OPTS is intentionally word-split to support normal rsync flags.
  # shellcheck disable=SC2086
  rsync -az --delete --partial --partial-dir=.rsync-partial -e "$remote_shell" ${HOMEAGENT_RSYNC_OPTS:-} \
    --exclude '.git/' \
    --exclude '.venv/' \
    --exclude '.mypy_cache/' \
    --exclude '.pytest_cache/' \
    --exclude '.ruff_cache/' \
    --exclude '__pycache__/' \
    --exclude '*.pyc' \
    --exclude '.DS_Store' \
    "$@"
}

install_key() {
  require_target

  if [[ -z "$SSH_KEY" ]]; then
    echo "No SSH key found. Generate one with:" >&2
    echo "  ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519_homeagent -C homeagent-deploy" >&2
    exit 1
  fi
  if [[ ! -f "$SSH_KEY.pub" ]]; then
    echo "Public key missing: $SSH_KEY.pub" >&2
    exit 1
  fi

  step "Installing public key $SSH_KEY.pub on $DEPLOY_TARGET"
  local pubkey
  pubkey="$(cat "$SSH_KEY.pub")"

  local ssh_args=()
  ssh_args+=("-o" "ConnectTimeout=${SSH_CONNECT_TIMEOUT}")
  ssh_args+=("-o" "ServerAliveInterval=${SSH_ALIVE_INTERVAL}")
  ssh_args+=("-o" "ServerAliveCountMax=${SSH_ALIVE_COUNT}")
  if [[ -n "${HOMEAGENT_SSH_OPTS:-}" ]]; then
    # shellcheck disable=SC2206
    ssh_args+=(${HOMEAGENT_SSH_OPTS})
  fi

  ssh ${ssh_args[@]+"${ssh_args[@]}"} "$DEPLOY_TARGET" "mkdir -p ~/.ssh && chmod 700 ~/.ssh && touch ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys && grep -qxF $(printf '%q' "$pubkey") ~/.ssh/authorized_keys || echo $(printf '%q' "$pubkey") >> ~/.ssh/authorized_keys"
  step "Installed. Testing key-based login"
  remote_bash "echo ok"
}

rsync_code_tree() {
  rsync_base --include '.env.example' --filter=':- .gitignore' "$@"
}

sync_code() {
  require_file "Dockerfile"
  require_file "docker-compose.yml"
  require_file "pyproject.toml"
  require_file "uv.lock"

  step "Syncing application code to $DEPLOY_TARGET:$DEPLOY_PATH"
  rsync_code_tree \
    --exclude '.env' \
    --exclude 'data/' \
    "$LOCAL_ROOT/" "$DEPLOY_TARGET:$DEPLOY_PATH/"
}

sync_env_and_data() {
  require_file ".env"
  require_file "data"

  step "Syncing .env and data to $DEPLOY_TARGET:$DEPLOY_PATH"
  rsync_base "$LOCAL_ROOT/.env" "$DEPLOY_TARGET:$DEPLOY_PATH/.env"
  rsync_base \
    --exclude 'backups/' \
    "$LOCAL_ROOT/data/" "$DEPLOY_TARGET:$DEPLOY_PATH/data/"
}

sync_env_only() {
  require_target
  require_file ".env"

  step "Syncing .env to $DEPLOY_TARGET:$DEPLOY_PATH"
  remote_bash "mkdir -p $(printf '%q' "$DEPLOY_PATH")"
  rsync_base "$LOCAL_ROOT/.env" "$DEPLOY_TARGET:$DEPLOY_PATH/.env"
}

bootstrap() {
  require_target
  step "Bootstrapping $DEPLOY_TARGET"
  remote_bash "
    set -euo pipefail
    mkdir -p $(printf '%q' "$DEPLOY_PATH")
    command -v docker >/dev/null
    if docker compose version >/dev/null 2>&1; then
      docker compose version
    elif command -v docker-compose >/dev/null 2>&1; then
      docker-compose version
    else
      echo 'Docker Compose is not installed on the remote host.' >&2
      exit 1
    fi
  "
}

deploy() {
  require_target
  bootstrap
  sync_code
  remote_compose "build"
  remote_compose "up -d"
  status
}

migrate() {
  require_target
  require_file ".env"
  require_file "data"

  step "Creating local pre-migration backup"
  (cd "$LOCAL_ROOT" && ./scripts/backup.sh "data/backups/pre-migration")

  step "Stopping local stack for a consistent data copy"
  (cd "$LOCAL_ROOT" && ./start.sh stop)

  bootstrap
  sync_code
  sync_env_and_data
  remote_compose "build"
  remote_compose "up -d"
  status

  cat <<EOF

Migration complete.
The local stack is still stopped. Start it again only if you are not using the
Mac mini as the active production instance:
  ./start.sh up
EOF
}

status() {
  require_target
  remote_compose "ps"
  step "Checking remote health endpoint"
  remote_bash "
    set -euo pipefail
    cd $(printf '%q' "$DEPLOY_PATH")
    curl --max-time $(printf '%q' "$HEALTH_TIMEOUT") -fsS http://127.0.0.1:8080/health || true
  "
}

case "$MODE" in
  bootstrap)
    bootstrap
    ;;
  deploy)
    deploy
    ;;
  migrate)
    migrate
    ;;
  up)
    require_target
    remote_compose "up -d"
    ;;
  down)
    require_target
    remote_compose "down"
    ;;
  restart)
    require_target
    remote_compose "build"
    remote_compose "up -d"
    status
    ;;
  status)
    status
    ;;
  logs)
    require_target
    step "Following remote logs. Press Ctrl-C to stop."
    remote_compose "logs -f"
    ;;
  logs-tail)
    require_target
    remote_compose "logs --tail=200"
    ;;
  backup)
    require_target
    remote_bash "
      set -euo pipefail
      cd $(printf '%q' "$DEPLOY_PATH")
      ./scripts/backup.sh
    "
    ;;
  install-key)
    install_key
    ;;
  sync-env)
    sync_env_only
    ;;
  help|-h|--help)
    usage
    ;;
  *)
    echo "Unknown command: $MODE" >&2
    usage >&2
    exit 2
    ;;
esac
