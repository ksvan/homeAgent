#!/usr/bin/env bash
#
# auto-update.sh — Pull latest main from GitHub if CI passed
#
# Runs on the Mac mini. Checks whether the latest commit on main passed CI
# before pulling and restarting the Docker Compose stack. Safe to run
# repeatedly — exits quietly if already up to date or CI has not passed yet.
#
# Usage:
#   ./scripts/auto-update.sh           # normal run
#   ./scripts/auto-update.sh --dry-run # check only, no pull/restart
#
# Cron example (every 15 minutes):
#   */15 * * * * $HOME/homeAgent/scripts/auto-update.sh >> $HOME/homeAgent/logs/auto-update.log 2>&1
#
# Requirements:
#   - curl, git, jq, docker (docker compose v2)
#   - GITHUB_TOKEN in .env (fine-grained PAT: read access on Actions + Contents)
#
# Local config (.env, data/, prompts/) is never touched — only source files are
# updated via git pull.

set -euo pipefail

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="${APP_DIR:-$(dirname "$_SCRIPT_DIR")}"

BRANCH="main"
# Set REPO to skip git-remote derivation (recommended for launchd/cron):
#   REPO=ksvan/homeAgent
REPO="${REPO:-}"
LOG_FILE="${LOG_FILE:-$APP_DIR/logs/auto-update.log}"
DRY_RUN=false

# Validate APP_DIR early so the error is obvious
if [ ! -d "$APP_DIR/.git" ]; then
    echo "[auto-update] ERROR: APP_DIR=$APP_DIR is not a git repository"
    exit 1
fi

if [ "${1:-}" = "--dry-run" ]; then
    DRY_RUN=true
fi

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

log() {
    echo "[auto-update] $(date '+%Y-%m-%d %H:%M:%S') $*"
}

# ---------------------------------------------------------------------------
# Dependency checks
# ---------------------------------------------------------------------------

for cmd in curl git jq docker; do
    if ! command -v "$cmd" &>/dev/null; then
        log "ERROR: required command not found: $cmd"
        exit 1
    fi
done

if ! docker compose version &>/dev/null; then
    log "ERROR: docker compose (v2 plugin) not found"
    exit 1
fi

# ---------------------------------------------------------------------------
# Load GITHUB_TOKEN and REPO from .env if not already set
# ---------------------------------------------------------------------------

ENV_FILE="$APP_DIR/.env"
if [ -f "$ENV_FILE" ]; then
    if [ -z "${GITHUB_TOKEN:-}" ]; then
        GITHUB_TOKEN=$(grep -E '^GITHUB_TOKEN=' "$ENV_FILE" | cut -d= -f2- | tr -d '"' | tr -d "'" | head -n1 || true)
    fi
    if [ -z "${REPO:-}" ]; then
        REPO=$(grep -E '^REPO=' "$ENV_FILE" | cut -d= -f2- | tr -d '"' | tr -d "'" | head -n1 || true)
    fi
fi

if [ -z "${GITHUB_TOKEN:-}" ]; then
    log "ERROR: GITHUB_TOKEN is not set. Add it to $APP_DIR/.env or export it before running."
    exit 1
fi

# ---------------------------------------------------------------------------
# Derive GitHub repo slug (or use REPO env var set in plist/cron)
# ---------------------------------------------------------------------------

if [ -z "$REPO" ]; then
    REMOTE_URL=$(git -C "$APP_DIR" remote get-url origin 2>/dev/null || true)
    if [ -z "$REMOTE_URL" ]; then
        log "ERROR: could not read git remote origin from $APP_DIR — set REPO=owner/repo to skip this"
        exit 1
    fi
    # Support https://github.com/owner/repo.git and git@github.com:owner/repo.git
    REPO=$(echo "$REMOTE_URL" \
        | sed 's|.*github\.com[:/]\(.*\)\.git$|\1|; s|.*github\.com[:/]\(.*\)$|\1|')
    if [ -z "$REPO" ]; then
        log "ERROR: could not parse GitHub owner/repo from remote: $REMOTE_URL"
        exit 1
    fi
fi

log "Repo: $REPO  Branch: $BRANCH"

# ---------------------------------------------------------------------------
# GitHub API helpers
# ---------------------------------------------------------------------------

gh_api() {
    local endpoint="$1"
    curl -sf \
        -H "Authorization: Bearer $GITHUB_TOKEN" \
        -H "Accept: application/vnd.github+json" \
        -H "X-GitHub-Api-Version: 2022-11-28" \
        "https://api.github.com$endpoint"
}

# ---------------------------------------------------------------------------
# 1. Get latest remote SHA on main
# ---------------------------------------------------------------------------

log "Fetching latest SHA for $BRANCH..."
remote_sha=$(gh_api "/repos/$REPO/commits/$BRANCH" | jq -r '.sha')

if [ -z "$remote_sha" ] || [ "$remote_sha" = "null" ]; then
    log "ERROR: could not fetch latest SHA from GitHub API"
    exit 1
fi

log "Remote SHA: ${remote_sha:0:12}"

# ---------------------------------------------------------------------------
# 2. Compare with local HEAD
# ---------------------------------------------------------------------------

local_sha=$(git -C "$APP_DIR" rev-parse HEAD)
log "Local SHA:  ${local_sha:0:12}"

if [ "$local_sha" = "$remote_sha" ]; then
    log "Already up to date. Nothing to do."
    exit 0
fi

# ---------------------------------------------------------------------------
# 3. Check CI status for the remote SHA
# ---------------------------------------------------------------------------

log "Checking CI status for ${remote_sha:0:12}..."

check_runs=$(gh_api "/repos/$REPO/commits/$remote_sha/check-runs")
total=$(echo "$check_runs" | jq '.total_count')

if [ "$total" = "0" ] || [ "$total" = "null" ]; then
    log "SKIP: no CI runs found for ${remote_sha:0:12} — may not have triggered yet"
    exit 0
fi

# Find the lint-and-test job specifically
ci_status=$(echo "$check_runs" | jq -r '.check_runs[] | select(.name == "lint-and-test") | .status' | head -n1)
ci_conclusion=$(echo "$check_runs" | jq -r '.check_runs[] | select(.name == "lint-and-test") | .conclusion' | head -n1)

if [ -z "$ci_status" ]; then
    log "SKIP: lint-and-test check run not found for ${remote_sha:0:12}"
    exit 0
fi

if [ "$ci_status" != "completed" ]; then
    log "SKIP: CI is still in progress (status=$ci_status) for ${remote_sha:0:12}"
    exit 0
fi

if [ "$ci_conclusion" != "success" ]; then
    log "SKIP: CI did not pass (conclusion=$ci_conclusion) for ${remote_sha:0:12}"
    exit 0
fi

log "CI passed for ${remote_sha:0:12}"

# ---------------------------------------------------------------------------
# 4. Pull and restart
# ---------------------------------------------------------------------------

if [ "$DRY_RUN" = true ]; then
    log "DRY RUN: would update ${local_sha:0:12} → ${remote_sha:0:12} and restart stack"
    exit 0
fi

log "Pulling ${local_sha:0:12} → ${remote_sha:0:12}..."
git -C "$APP_DIR" pull --ff-only origin "$BRANCH"

pulled_sha=$(git -C "$APP_DIR" rev-parse HEAD)
if [ "$pulled_sha" != "$remote_sha" ]; then
    log "ERROR: git pull succeeded but HEAD is ${pulled_sha:0:12}, expected ${remote_sha:0:12}"
    exit 1
fi

log "Rebuilding and restarting stack..."
docker compose -f "$APP_DIR/docker-compose.yml" up -d --build

log "Update complete: ${local_sha:0:12} → ${remote_sha:0:12}"
