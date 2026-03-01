# Observability

Logging, health checks, system status, and cost visibility for HomeAgent.

---

## Structured Logging

All logging uses **structlog** with JSON output in production and human-readable console output in development. Plain `print()` and `logging.info()` are not used directly — all log calls go through structlog.

### Log format (production)

```json
{
  "timestamp": "2026-03-01T08:32:11.123Z",
  "level": "info",
  "event": "agent_run_complete",
  "user_id": "abc123",
  "household_id": "xyz",
  "model": "claude-sonnet-4-5",
  "duration_ms": 1243,
  "tokens_input": 2847,
  "tokens_output": 312,
  "tools_called": ["homey_device_set_capability"],
  "trace_id": "uuid"
}
```

### Log format (development)

Human-readable with colour highlighting, controlled by `LOG_FORMAT=console` in `.env`.

### Log levels

| Level | When to use |
| --- | --- |
| `DEBUG` | Detailed internals — prompt assembly, tool args, memory retrieval scores |
| `INFO` | Normal operations — agent run, tool call, message received |
| `WARNING` | Degraded state — Homey unreachable, stale cache, memory retrieval skipped |
| `ERROR` | Failures requiring attention — DB write failed, LLM provider down, scheduler crash |
| `CRITICAL` | Service-level failures — both LLM providers down, DB unreadable |

Set via `LOG_LEVEL` in `.env`.

### Trace IDs

Each incoming webhook request is assigned a `trace_id` (UUID) at the FastAPI middleware layer. All log entries within that request share the same `trace_id`, making it easy to follow a single conversation turn through the logs.

---

## Health Endpoint

`GET /health` — returns component status. Used by Docker healthcheck and optionally by external monitoring.

**Healthy response (HTTP 200):**

```json
{
  "status": "healthy",
  "version": "0.1.0",
  "uptime_seconds": 86400,
  "components": {
    "database": "ok",
    "chroma": "ok",
    "homey_mcp": "ok",
    "scheduler": "ok",
    "llm_primary": "ok",
    "llm_fallback": "ok"
  },
  "last_homey_sync": "2026-03-01T08:30:00Z"
}
```

**Degraded response (HTTP 200, status = "degraded"):**

```json
{
  "status": "degraded",
  "components": {
    "database": "ok",
    "chroma": "ok",
    "homey_mcp": "unreachable",
    "scheduler": "ok",
    "llm_primary": "ok",
    "llm_fallback": "ok"
  },
  "last_homey_sync": "2026-03-01T07:45:00Z"
}
```

**Unhealthy response (HTTP 503):** returned only if the database is down or the LLM layer is completely unavailable.

Docker Compose healthcheck:

```yaml
healthcheck:
  test: ["CMD", "curl", "-f", "http://localhost:8080/health"]
  interval: 30s
  timeout: 5s
  retries: 3
  start_period: 15s
```

---

## Admin Status Command

Any admin can send `/status` to get a real-time summary in Telegram:

```
HomeAgent status — 01 Mar 2026, 08:32

Uptime: 3 days, 2 hours
Version: 0.1.0

Components:
  ✅ Database
  ✅ Homey (last sync: 2 min ago)
  ✅ Claude Sonnet 4.5
  ✅ Scheduler (12 jobs pending)

This week:
  Messages: 47
  LLM calls: 83
  Est. cost: ~$0.42

Active tasks: 1
  - "Plan weekend dinner" (Kristian, since 10:15)
```

---

## Cost Visibility

### Per-run tracking

Every agent run logs `tokens_used: {input: N, output: N}` and `model_used` to `agent_run_log`. This is the raw data for all cost calculations.

### Weekly summary (scheduled)

A scheduled job runs every Monday at 08:00 (household timezone) and sends a summary to all admins:

```
HomeAgent weekly summary — week of 24 Feb

Conversations: 47  (↑12 from last week)
LLM calls: 83
  Claude Sonnet 4.5: 61 calls, ~1.2M tokens
  Claude Haiku 4.5: 22 calls, ~340K tokens
  GPT-4o: 0 calls (fallback unused)

Estimated cost: ~$1.84
  (Claude: ~$1.61, OpenAI embeddings: ~$0.23)

Home actions: 34
  Confirmed: 5, Immediate: 29
  Failures: 0

Top users this week:
  Kristian: 28 messages
  Emma: 12 messages
  Sofie: 7 messages
```

Cost estimates use fixed per-token rates configured in `.env`. They are estimates only — check your Anthropic and OpenAI dashboards for exact billing.

### Cost config

```env
COST_ESTIMATE_CLAUDE_SONNET_INPUT=0.000003    # $ per token
COST_ESTIMATE_CLAUDE_SONNET_OUTPUT=0.000015
COST_ESTIMATE_CLAUDE_HAIKU_INPUT=0.00000025
COST_ESTIMATE_CLAUDE_HAIKU_OUTPUT=0.00000125
COST_ESTIMATE_GPT4O_INPUT=0.0000025
COST_ESTIMATE_GPT4O_OUTPUT=0.00001
COST_ESTIMATE_EMBEDDING=0.00000002
```

---

## Log Retention and Rotation

Logs are written to stdout (captured by Docker). Structured log entries are also persisted in `event_log` and `agent_run_log` in SQLite, with configurable retention (default 90 days).

For file-based log archiving, configure Docker's log driver (e.g. `json-file` with `max-size` and `max-file` options in `docker-compose.yml`).

---

## Future: External Monitoring

If you want uptime alerting without checking Telegram, the `/health` endpoint can be polled by:

- **UptimeRobot** (free tier, checks every 5 min, alerts via email/Telegram)
- **Healthchecks.io** (ping-based, great for scheduled jobs too)
- **Prometheus + Grafana** (overkill for home use, but possible)

A `GET /metrics` endpoint in Prometheus format is not implemented by default but can be added via the `prometheus-fastapi-instrumentator` library if needed.
