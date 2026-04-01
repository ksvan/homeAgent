# Operations Runbook

## Backups

### Automated Backup Script

**Script**: `scripts/backup.sh`

Backs up all SQLite databases (`users.db`, `memory.db`, `cache.db`) via `sqlite3 .dump | gzip` and the ChromaDB directory as a tarball. Safe with WAL mode.

```bash
# Manual run
./scripts/backup.sh

# Custom destination
./scripts/backup.sh /mnt/nas/backups

# Cron (daily at 3 AM)
0 3 * * * /path/to/homeAgent/scripts/backup.sh >> /var/log/homeagent-backup.log 2>&1
```

**Retention**: 14 days (configurable via `RETENTION_DAYS` in the script).

### Restore from Backup

```bash
# Decompress
gunzip < data/backups/users-20260331-030000.sql.gz > restore.sql

# Restore (replaces existing data)
sqlite3 data/db/users.db < restore.sql

# ChromaDB
tar -xzf data/backups/chroma-20260331-030000.tar.gz -C data/
```

## Docker Resource Limits

Defined in `docker-compose.yml`:

| Service | Memory | CPUs | Grace period |
|---------|--------|------|--------------|
| homeagent | 2 GB | 2 | 30s |
| tools | 1 GB | 1 | 30s |
| prometheus-mcp | 512 MB | 0.5 | 30s |
| cloudflared | 256 MB | 0.5 | 30s |

Adjust `mem_limit` and `cpus` in `docker-compose.yml` if needed. Monitor with `docker stats`.

## SQLite Resilience

Pragmas applied to all database connections (`app/db.py`):

- `journal_mode=WAL` — concurrent readers, single writer
- `busy_timeout=5000` — retry writes for up to 5 seconds instead of immediate SQLITE_BUSY
- `foreign_keys=ON` — enforce referential integrity

## MCP Connection Retry

All three MCP clients (Homey, Tools, Prometheus) use the same retry pattern on startup:

- **Timeout**: 10 seconds per attempt
- **Max retries**: 3
- **Backoff**: 5 seconds between retries
- **On failure**: Service is disabled for the session (tools won't be available)

If Homey MCP is down after startup, the health endpoint returns `"degraded"`.

Manual reconnect: use the admin UI `/status refresh` button or restart the container.

## Health Endpoint

`GET /health` returns:

- `"healthy"` — all DBs writable, Homey MCP connected
- `"degraded"` — DBs OK but Homey MCP disconnected, or a DB is read-only/missing

## Background Task Errors

Fire-and-forget tasks (memory extraction, summarization, world model sync) emit `run.background_error` events on failure. These appear in the admin dashboard SSE stream.

The SSE subscriber queue (maxsize=200) logs a warning on overflow rather than silently dropping events.

## Conversation Turn Retention

`app/scheduler/cleanup.py` keeps the last 200 conversation turns per user (configurable via `_TURNS_MAX_KEEP`). The cleanup job runs on the APScheduler cron.
