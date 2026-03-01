# Graceful Degradation

HomeAgent should remain partially functional when individual dependencies are unavailable. This document defines the failure contract for each component: what the agent says, what continues to work, and what is blocked.

The guiding principle: **always tell the user what is wrong, never fail silently, and keep unaffected functionality running.**

---

## Degradation Matrix

| Component fails | User-facing impact | What still works |
| --- | --- | --- |
| Homey MCP unreachable | Home control disabled | All non-home features |
| Anthropic API down | Falls back to GPT-4o | Full functionality via fallback |
| OpenAI API down | Falls back to Anthropic | Full functionality; embeddings degrade (see below) |
| Both LLM providers down | Agent responds with error | Scheduled reminders still fire |
| Chroma (vector store) down | Episodic memory retrieval disabled | All other features; conversations still work without semantic recall |
| SQLite read failure | Full failure for that request | Other requests unaffected |
| SQLite write failure | Request fails; response still sent | DB state may be inconsistent — logged prominently |
| Telegram API unreachable | No outbound messages | Inbound processing still runs; messages queued if possible |
| APScheduler crash | Reminders and cron jobs stop | Conversational agent still responds |

---

## Per-Component Contracts

### Homey MCP

**Startup:** Connect to Homey MCP at startup. If unreachable, log a warning and continue — the agent starts without home tools registered. The device snapshot cache is served stale from the last successful poll.

**During operation:** If a Homey tool call fails (timeout, 5xx, connection reset):
1. Retry once after 2 seconds
2. If still failing: respond to user — *"I can't reach your home right now. The last known state was [timestamp]. Try again in a moment."*
3. Log the failure to `event_log` with `event_type = "homey_error"`
4. Do not retry indefinitely — surface the problem immediately

**Recovery:** Homey MCP connection is re-attempted on every incoming request. When it succeeds again, the agent returns to normal without restart.

### LLM Providers

See [agent-design.md](agent-design.md#fallback-behaviour) for the retry/fallback sequence.

**If both providers fail:** The agent responds — *"I'm having trouble reaching my AI services right now. Please try again in a moment."* Scheduled reminders and cron jobs continue firing.

**Embeddings:** If OpenAI is unavailable, embedding generation fails. The episodic memory retrieval step is skipped (no relevant memories injected), but the conversation continues with profile context only. This is transparent to the user — the agent just has less recalled context.

### Chroma (Vector Store)

**If Chroma fails to initialise or crashes:** Log the error. Disable episodic memory retrieval for that session. All other memory layers (structural profiles, conversation history) continue to function normally. The agent does not inform the user unless asked.

**Recovery:** Chroma client is re-initialised on next request.

### SQLite

**Read failures:** The affected request fails. The user receives a generic error: *"Something went wrong on my end. Please try again."* Logged as a critical error.

**Write failures (non-critical paths):** If saving a memory or logging a run fails, the user's response is still sent. The failure is logged prominently. Data consistency may be affected — alert the admin.

**Write failures (critical paths):** If saving a `pending_action` or `reminder` fails, the operation is aborted and the user is informed — *"I wasn't able to save that — please try again."*

**WAL mode:** SQLite is opened in WAL (Write-Ahead Logging) mode to reduce write contention from concurrent webhook requests.

### Rate Limiting

If a user exceeds the rate limit, they receive: *"You're sending messages quite quickly — please wait a moment before trying again."* No agent processing occurs. Logged to `event_log`.

### APScheduler

If the scheduler fails to start or a job crashes:
1. Log the error with full traceback
2. Send a Telegram message to all admins: *"[HomeAgent] Scheduler error — reminders may not fire. Check logs."*
3. APScheduler will attempt to recover failed jobs on next run

---

## Startup Sequence and Partial Readiness

On container start, components initialise in this order:

```text
1. Load config (.env) — fail hard if required vars missing
2. Initialise SQLite databases + run Alembic migrations
3. Initialise Chroma — warn and continue if fails
4. Connect to Homey MCP — warn and continue if fails
5. Start APScheduler
6. Start FastAPI server
7. Register Telegram webhook (production) or start polling (development)
8. Log "HomeAgent ready" with component status summary
```

At step 8, the agent sends a startup message to all admin Telegram IDs:

```
HomeAgent started.
✅ Database: OK
✅ Scheduler: OK
⚠️  Homey: unreachable (will retry on next request)
✅ LLM: OK (Claude Sonnet 4.5)
```

This gives the admin immediate visibility into partial startup without having to check logs.

---

## Admin Alerting

The following events trigger a Telegram message to all admins:

| Event | Message |
| --- | --- |
| Container start | Startup status summary (see above) |
| Homey MCP unreachable for > 15 minutes | *"Homey has been unreachable for 15 min"* |
| Both LLM providers failing | *"All LLM providers are failing — agent non-functional"* |
| Scheduler crash | *"Scheduler error — reminders may not fire"* |
| SQLite write failure | *"Database write error — check logs"* |
| Rate limit sustained (same user > 5 min) | *"[username] has been hitting rate limits repeatedly"* |

Alert cooldown: the same alert is not re-sent within 30 minutes to avoid spam. Configurable via `ALERT_COOLDOWN_MINUTES`.
