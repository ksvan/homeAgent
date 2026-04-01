#!/usr/bin/env bash
#
# backup.sh — Daily SQLite + ChromaDB backup for HomeAgent
#
# Usage:
#   ./scripts/backup.sh              # uses default paths
#   ./scripts/backup.sh /custom/dir  # override backup destination
#
# Intended to run via cron:
#   0 3 * * * /path/to/homeAgent/scripts/backup.sh >> /var/log/homeagent-backup.log 2>&1
#
# Retention: keeps the last 14 daily backups per database.

set -euo pipefail

DATA_DIR="${DATA_DIR:-data}"
BACKUP_DIR="${1:-data/backups}"
RETENTION_DAYS=14

DB_DIR="$DATA_DIR/db"
CHROMA_DIR="$DATA_DIR/chroma"
TIMESTAMP=$(date +%Y%m%d-%H%M%S)

mkdir -p "$BACKUP_DIR"

backup_sqlite() {
    local db_name="$1"
    local db_path="$DB_DIR/$db_name.db"
    local out_path="$BACKUP_DIR/${db_name}-${TIMESTAMP}.sql.gz"

    if [ ! -f "$db_path" ]; then
        echo "[backup] SKIP $db_name — file not found: $db_path"
        return
    fi

    # Use .dump for a consistent, portable backup (works even with WAL)
    sqlite3 "$db_path" ".dump" | gzip > "$out_path"
    local size
    size=$(du -h "$out_path" | cut -f1)
    echo "[backup] OK   $db_name → $out_path ($size)"
}

backup_chroma() {
    local out_path="$BACKUP_DIR/chroma-${TIMESTAMP}.tar.gz"

    if [ ! -d "$CHROMA_DIR" ]; then
        echo "[backup] SKIP chroma — directory not found: $CHROMA_DIR"
        return
    fi

    tar -czf "$out_path" -C "$DATA_DIR" chroma
    local size
    size=$(du -h "$out_path" | cut -f1)
    echo "[backup] OK   chroma → $out_path ($size)"
}

cleanup() {
    local pattern="$1"
    find "$BACKUP_DIR" -name "$pattern" -type f -mtime +$RETENTION_DAYS -delete 2>/dev/null || true
    local remaining
    remaining=$(find "$BACKUP_DIR" -name "$pattern" -type f 2>/dev/null | wc -l | tr -d ' ')
    echo "[backup] CLEAN $pattern — $remaining backups retained"
}

echo "=== HomeAgent backup $TIMESTAMP ==="

backup_sqlite "users"
backup_sqlite "memory"
backup_sqlite "cache"
backup_chroma

cleanup "users-*.sql.gz"
cleanup "memory-*.sql.gz"
cleanup "cache-*.sql.gz"
cleanup "chroma-*.tar.gz"

echo "=== Backup complete ==="
