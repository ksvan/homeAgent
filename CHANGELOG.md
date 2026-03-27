# Changelog

All notable changes to HomeAgent are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning follows [Semantic Versioning](https://semver.org/).

---

## Unreleased

### Added

#### Per-slot API keys for explicit LLM provider binding

- **`app/config.py`** — 5 new optional `MODEL_*_API_KEY` settings (primary, background, fallback, background_fallback, embedding). When set, each slot uses its own key and provider rather than sharing global keys, eliminating silent provider/model mismatches.
- **`app/agent/llm_router.py`** — Replaced inline provider inference with `_resolve_key` + `_make_model` helpers. Provider is determined by key prefix (`sk-ant-` → Anthropic, else OpenAI). Fully backward compatible — existing `.env` files with only global keys need no changes.
- **`app/memory/episodic.py`** — `_get_embedding` prefers `MODEL_EMBEDDING_API_KEY` over the global `OPENAI_API_KEY`.
- **`docs/frameworks-and-services.md`** — New reference document listing all frameworks, libraries, and external services with descriptions of their general purpose and their specific role in HomeAgent.

### Improved

#### Docker image 31% smaller (1.34 GB → 924 MB)

- **`Dockerfile`** — Moved `adduser` before `uv sync` and replaced `chown -R` with `COPY --chown` flags. The previous pattern duplicated the entire 727 MB `.venv` tree into a separate ownership layer; the fix eliminates that waste entirely.

### Planned

- Channels: email, iMessage, voice
- TTS via Homey (cast to Google Nest etc.)
- Home awareness / anomaly detection (Prometheus baseline jobs)
- Improved memory: associate scenarios (e.g. "goodnight") with device action sets

---

## [0.11.1] - 2026-03-25

### Added

#### `/prompts` slash command

- **`app/commands/handlers.py`** — `_ScheduledPrompts` command (`/prompts`): lists all household scheduled prompts with ID, status, recurrence, time, and prompt text. `/prompts run <id-prefix>` triggers an immediate execution — useful for testing and debugging scheduled prompts.

#### `/status refresh` subcommand

- **`app/commands/handlers.py`** — `/status refresh` reconnects any disconnected MCP services (Homey, Prometheus, Tools) without a container restart: calls `stop_mcp()` + `start_mcp()` per service, then `reload_agent()` so the new connections are used immediately.

### Fixed

#### Scheduled prompt `format_map` crash on JSON examples in instructions.md

- **`app/agent/prompts.py`** — `_SafeStr` class: overrides `__format__` to reconstruct `{key:spec}` for unknown format keys instead of raising `ValueError: Invalid format specifier`. Fixes crash when `instructions.md` contains JSON examples like `{"query": "lights bedroom"}` which `str.format_map()` tried to parse as a format spec.
- `_SafeDict.__missing__` now returns `_SafeStr(key)` instead of a plain `str`, preserving the protection for arbitrary format specs.

#### Scheduled prompt silent failures

- **`app/scheduler/jobs.py`** — `_fire_scheduled_prompt_inner`: added `logger.info` at job start; wrapped DB lookup in its own `try/except` with `logger.error` + re-raise; upgraded `logger.warning` → `logger.error` for all failure paths; error reply now includes the actual exception text so failures are visible in logs.

#### `prom_query_range` crash when agent reverses timestamps

- **`app/agent/agent.py`** — `Agent` constructor now sets `retries=3` (was default 1): gives the LLM 3 attempts to self-correct a bad tool call before the run is abandoned.
- **`services/prometheus-mcp/app/guards.py`** — `validate_range` error message now includes the actual `start`/`end` values and an explicit hint to swap them, making self-correction possible within the retry budget.
- **`services/prometheus-mcp/app/mcp_server.py`** — `prom_query_range` docstring clarifies that `start` is the older/earlier time and `end` is the newer/later time (usually now), with `Must be before/after` notes.

---

## [0.11.0] - 2026-03-22

### Added

#### SharePoint file access

- **`services/tools-mcp/app/mcp_server.py`** — two new tools behind `FEATURE_SHAREPOINT=true`:
  - `sharepoint_list_files(site_url, folder_path)` — lists files and subfolders in a SharePoint document library via the SharePoint REST API; SSRF-checked.
  - `sharepoint_download_file(file_url)` — downloads a file; auto-parses `.docx` to plain text (paragraphs + tables), decodes `.txt`/`.csv`/`.md` as UTF-8, reports binary types.
- **`services/tools-mcp/requirements.txt`** — added `python-docx>=1.1`.
- **`services/tools-mcp/app/config.py`** — `feature_sharepoint`, `sharepoint_timeout_seconds`, `sharepoint_max_file_bytes`, `sharepoint_max_content_bytes` settings.
- **`services/tools-mcp/app/main.py`** — SharePoint registered in feature-gate startup log.
- **`docker-compose.yml`** — `FEATURE_SHAREPOINT: "true"` added to `tools` service environment.

#### Prometheus MCP service

- **`services/prometheus-mcp/Dockerfile`** (new) — missing Dockerfile for the prometheus-mcp container; based on `uv:python3.12-bookworm-slim`, runs as non-root `appuser`.
- **`services/prometheus-mcp/app/config.py`** — fixed `IndexError` crash at container startup: `.env` lookup now walks parents safely instead of hardcoding `parents[3]`.

#### HTTP requests from Python scripts

- **`prompts/instructions.md`** — `## Bash commands`: notes that `curl` is unavailable (blocked); `## Python scripts`: updated to state that scripts may make HTTP requests using the pre-installed `httpx`.

### Fixed

#### Homey home structure truncated in large homes

- **`app/homey/mcp_client.py`** — raised `_MAX_TOOL_RESULT_CHARS` from `12,000` to `40,000` chars so that `homey_get_home_structure` returns a complete response for large homes instead of truncating at 12k.

#### tools-mcp crash on startup with pydantic-settings ≥2.4

- **`services/tools-mcp/app/config.py`** — `bash_allowed_commands` changed from `list[str]` to `str` (CSV). pydantic-settings ≥2.4 JSON-parses list fields from env vars before field validators run, breaking comma-separated allowlists. Field is now a plain string with a `bash_allowed_commands_list()` helper method.
- **`services/tools-mcp/app/mcp_server.py`** — updated bash tool to call `settings.bash_allowed_commands_list()`.

### Changed

#### Context window reduced — conversation history compaction

- **`app/memory/conversation.py`**:
  - `_MAX_RECENT_PAIRS` lowered from 20 → 10 (halves the number of turns in the sliding window).
  - `_FULL_TURNS_KEPT = 3` — full tool-call results kept only for the 3 most recent turns; older turns have `ToolReturnPart` content replaced with `[result omitted]`, eliminating large tool response blobs from older history.
  - `_SUMMARY_THRESHOLD` lowered from 50 → 20; `_SUMMARY_BATCH` from 30 → 10 — summarization triggers much earlier, keeping a rolling summary in the system prompt.
  - `_strip_tool_results()` helper strips `ToolReturnPart` content from older turns when loading.
- **`app/scheduler/cleanup.py`** — `purge_old_turns` daily cleanup job added: retains max 50 `ConversationTurn` rows per user, pruning the rest to prevent unbounded DB growth.

---

## [0.10.0] - 2026-03-13

### Added

#### Scheduled prompts

- **`app/models/scheduled_prompts.py`** (new) — `ScheduledPrompt` SQLModel: recurring LLM prompts stored in `users.db` with `id`, `household_id`, `user_id`, `channel_user_id`, `name`, `prompt`, `recurrence`, `time_of_day`, `enabled`, `created_at`.
- **`alembic/versions/0004_users_db_scheduled_prompts.py`** (new) — migration creating `scheduledprompt` table with indices on `household_id` and `user_id`.
- **`app/scheduler/scheduled_prompts.py`** (new) — `_build_trigger` (maps `"daily"` / `"weekly:sun"` / `"monthly:15"` to APScheduler `CronTrigger`), `create_scheduled_prompt`, `remove_scheduled_prompt`, `restore_scheduled_prompts` (re-registers all enabled prompts on startup with `ConflictPolicy.replace`).
- **`app/agent/tools/scheduled_prompts.py`** (new) — three agent tools: `schedule_prompt`, `list_scheduled_prompts`, `cancel_scheduled_prompt`.
- **`app/scheduler/jobs.py`** — `fire_scheduled_prompt` job: resolves user/household names from DB, calls `run_conversation`, delivers result to channel, emits `job.fire` / `job.complete` / `job.error` control events.
- **`app/api/server.py`** — `restore_scheduled_prompts()` called in lifespan startup after other restore calls.
- **`app/agent/agent.py`** — `register_scheduled_prompt_tools(a)` registered on agent creation.
- **`prompts/instructions.md`** — `## Scheduled prompts` section added; instructions for when and how to use `schedule_prompt`.
- **`app/control/api.py`** — admin Scheduler tab extended with third section "Scheduled Prompts" (Name | Schedule | Time | Prompt | ID); backend `GET /admin/scheduler` returns `scheduled_prompts` list.

#### `start.sh clean` subcommand

- **`start.sh`** — `./start.sh clean` prunes dangling Docker images and volumes to free disk space. Safe to run while the stack is up (stack uses bind mounts, not named volumes).

### Fixed

#### Agent describes actions but doesn't execute them

- **`prompts/instructions.md`** — added `## Tool execution` section at the top: establishes a global contract that deciding to act = calling the tool in the same response. Includes explicit wrong/right examples covering scheduling, memory, and device control. Reinforces per-feature "always call immediately" rules with a behavioural contract.

#### 429 rate limit errors crashing requests

- **`app/config.py`** — `model_primary` default upgraded from `claude-sonnet-4-5` to `claude-sonnet-4-6`.
- **`app/bot.py`** — retry loop for `ModelHTTPError` 429 in `handle_incoming_message`: up to 2 retries with 5 s / 15 s backoff. User notified once ("rate limited — retrying shortly") on first 429.
- **`app/homey/mcp_client.py`** — `_MAX_TOOL_RESULT_CHARS = 12_000` cap on MCP tool responses in `_policy_process_tool_call`. Addresses root cause: `homey_get_home_structure` returning 20k+ chars across multiple tool calls per run pushing over the 30k input tokens/minute org limit.

---

## [0.10.3] - 2026-03-13

### Security

#### SBP-001 (Critical) — Policy gate enforced for scheduled Homey actions

- **`app/agent/tools/actions.py`** — `schedule_homey_action` now calls `evaluate_policy` at schedule time. If the inner tool requires confirmation, the agent returns an error immediately rather than queuing a future unattended execution.
- **`app/scheduler/jobs.py`** — `execute_homey_action` now calls `evaluate_policy` at execution time before calling `direct_call_tool`. If `requires_confirm=True`, the action is skipped, the user is notified with a clear message, and the task is marked FAILED.

#### SBP-002 (High) — Admin `APP_SECRET_KEY` validated at startup in production

- **`app/config.py`** — Extended `_require_webhook_secret` validator: in `production` mode, raises `ValueError` at startup if `APP_SECRET_KEY` is empty or shorter than 32 characters. Prevents misconfigured deployments from silently exposing the admin dashboard.

#### SBP-003 (High) — Admin token stripped from page URL (no longer in browser history)

- **`app/control/api.py`** — Admin JS now uses `history.replaceState` on page load to strip `?token=` from the address bar before it enters browser history or bookmarks. Token is stored in `sessionStorage`. All `fetch()` calls use `Authorization: Bearer` header instead of URL query parameter. EventSource still uses `?token=` (browsers cannot send custom headers for SSE; the URL is JS-constructed and not stored in history).
- **`app/control/auth.py`** — Updated docstring to document the two-path auth model and why `?token=` is retained exclusively for the EventSource connection.

#### SBP-004 (Medium) — SSRF guard in scrape tool

- **`services/tools-mcp/app/mcp_server.py`** — Added `_is_ssrf_blocked(url)` helper that resolves the hostname via `socket.getaddrinfo` and blocks requests to private (RFC-1918), loopback, link-local, and multicast ranges. Applied to `scrape_page` before the HTTP request. Changed `follow_redirects=True` → `follow_redirects=False` to prevent redirect-based SSRF bypass.

#### SBP-005 (Medium) — Webhook body-size cap

- **`app/api/webhooks.py`** — Added `_MAX_BODY_BYTES = 64 KB` guard. Checks `Content-Length` header first (early rejection), then verifies raw body size after read. Returns HTTP 413 for oversized payloads before JSON parsing. Telegram updates are typically <10 KB.

---

## [0.10.2] - 2026-03-13

### Fixed

#### Resilience Fixes — Priority 2 (7 fixes)

- **Fix 9 — Rate limiter memory leak** (`app/bot.py`): `_user_call_times` now deletes empty list entries after window expiry so idle-user keys don't accumulate forever.

- **Fix 10 — Calendar error sanitization** (`app/agent/tools/calendar.py`): `add_calendar` no longer exposes the raw `httpx` exception (which can include auth tokens in URL query strings); replaced with a generic "check it is correct and accessible" message.

- **Fix 11 — Cleanup job isolation + staggered triggers** (`app/scheduler/cleanup.py`): Each of the three daily cleanup jobs (`purge_old_logs`, `purge_stale_memories`, `purge_old_tasks`) is now wrapped in `try/except Exception: logger.exception(...)` so a DB error in one job doesn't silently abort the others. Triggers are staggered by 1-hour `start_delay` so all three don't fire simultaneously on first startup.

- **Fix 12 — Cascade delete for vector index entries** (`app/agent/tools/memory.py`, `app/memory/episodic.py`): `forget_memory` now calls `_delete_from_vec` before `session.delete` (inside the session) so vec rows are cleaned up atomically with their parent records. Added a doc comment to `_insert_into_vec` documenting the known `embedding_id=NULL` degraded-mode behaviour for records whose vec insert failed.

- **Fix 13 — Expose degraded startup state** (`app/api/health.py`, `app/api/server.py`): `/health` now reports `mcp_prom` and `mcp_tools` status alongside `mcp_homey`. Startup logs a summary line: `MCP startup: homey=ok prom=ok tools=ok`. Renamed `mcp` key to `mcp_homey` in the health response for clarity.

- **Fix 14 — Deduplicate concurrent scheduled prompt runs** (`app/scheduler/jobs.py`): Added module-level `_running_prompts: set[str]` with `try/finally` guard in `fire_scheduled_prompt`. If a second APScheduler trigger fires before the first run completes, the duplicate logs a warning and returns immediately without starting another agent run.

- **Fix 15 — Circuit breaker for embedding API** (`app/memory/episodic.py`): `_get_embedding` now tracks consecutive failures. After 3 failures it opens the circuit for 60 s, skipping all HTTP calls during that window. Resets on success. Prevents every `store_memory` / `search_memories` call from making a failing HTTP request when the OpenAI embedding API is down.

---

## [0.9.0] - 2026-03-09

### Changed

#### Docker-only runtime — dev mode removed

- **`app/__main__.py`** — removed `_run_development()` (Telegram long-polling mode) and the `APP_ENV` branch. Entry point is now ~80 lines: run migrations, start two uvicorn servers (main + admin), shut down cleanly on SIGTERM.
- **`start.sh`** — removed `dev` mode (uv subprocess runner). Script is now Docker-only: `./start.sh` builds and starts the stack, with `logs`, `stop`, and `restart` subcommands.
- **`docs/development.md`** — updated local setup to reflect Docker-only workflow.

#### Admin panel isolated to LAN-only port

- **`app/config.py`** — added `admin_port: int = 9090` (`ADMIN_PORT` in `.env`).
- **`app/__main__.py`** — admin FastAPI app now runs on `settings.admin_port` (default 9090) as a second uvicorn server in the same process. Shares in-process state (event bus, scheduler) with the main app.
- **`app/api/server.py`** — admin router removed from `create_app()`. Port 8080 now serves only `/health` and `/webhook/telegram`.
- **`docker-compose.yml`** — added `"9090:9090"` port binding. Port 8080 is what cloudflared proxies; port 9090 is LAN-only and never reaches the internet.
- **`.env.example`** — documented `ADMIN_PORT=9090`.

The admin panel is now reachable at `http://<lan-ip>:9090/admin`. Auth (APP_SECRET_KEY) is unchanged.

---

## [0.8.0] - 2026-03-09

### Added

#### Scheduled device actions

- **`app/agent/tools/actions.py`** (new) — three agent tools: `schedule_homey_action` (persist + schedule a future Homey action), `list_scheduled_actions` (show pending actions for the current user), `cancel_scheduled_action` (cancel by ID)
- **`app/scheduler/actions.py`** (new) — `schedule_action()` creates a `Task` record and registers an APScheduler `DateTrigger` job; `restore_pending_actions()` re-registers future actions from DB on startup (overdue tasks are marked FAILED)

### Fixed

#### Homey tools not being called by the agent

- **`app/homey/mcp_client.py`** — `_SIMPLE_TOOLS` updated from old direct-API tool names (`list_devices`, `set_devices_capabilities_values`, etc.) to the Homey AI Chat Control meta-tool pattern: `homey_search_tools`, `homey_use_tool`, `homey_get_home_structure`, `homey_get_states`, `homey_get_flow_overview`. This was the root cause of the agent silently ignoring home-control requests.

#### Telegram confirmation double-press race condition

- **`app/channels/telegram.py`** — `delete_pending_action(token)` moved to immediately after the ownership check, *before* `query.answer()` and `direct_call_tool`. Any subsequent press on the same token now gets "expired or already handled" immediately, preventing double-execution when the user pressed again while waiting for a slow Homey response.

#### Policy gate seeder not updating existing rows

- **`app/policy/seeder.py`** — changed from insert-only to upsert: existing rows are updated to match `default_policies.py` on every startup. `default_policies.py` is now the source of truth.

### Changed

#### Policy: no confirmation for individual device operations

- **`app/policy/default_policies.py`** — `use_tool` policy changed to `requires_confirm=False`. Individual device actions (turn on/off a single light, set temperature, etc.) now execute immediately without a confirmation prompt.
- **`app/policy/gate.py`** — confirmation message for `use_tool` is now built dynamically from the inner tool name (e.g. `"Execute Homey action 'set_light_bedroom'?"`) rather than using the generic policy message.
- **`prompts/instructions.md`** — agent asks conversationally before: bulk zone/floor/whole-house operations, alarm arm/disarm, door lock/unlock, or any change involving 3+ devices. Single-device operations execute immediately; agent does not announce it is sending a confirmation.

#### Home profile discovery updated for AI Chat Control

- **`app/homey/home_profile.py`** — profile discovery now calls `get_home_structure` (single call returning zones + devices + moods) instead of the removed `get_zones` / `get_devices` tools.

#### Admin panel auth accepts query-param token

- **`app/control/auth.py`** — `require_admin_auth` now accepts `?token=<key>` query param in addition to `Authorization: Bearer` header, enabling plain browser navigation to protected admin routes.
- **`app/control/api.py`** — admin UI propagates `?token=` to all sub-requests (stats, memory, SSE stream) so the panel works end-to-end when opened via URL with token.

### Removed

- **`app/agent/tools/bash.py`**, **`python_exec.py`**, **`scrape.py`**, **`search.py`** — dead files; these tools were moved to the `services/tools-mcp/` container in v0.7.0 but not deleted from the main app.
- **`app/shell.py`** — bash runner implementation, now lives in `services/tools-mcp/app/shell.py`.

---

## [0.7.1] - 2026-03-08

### Security

- **`app/control/auth.py`** — new `require_admin_auth` FastAPI dependency; optional bearer token protecting all `/admin/*` routes using the existing `APP_SECRET_KEY` config value; open when key is unset (dev mode unchanged)
- **`app/api/server.py`** — admin router mounted with `require_admin_auth` dependency; `openapi_url=None` suppresses schema discovery endpoint
- **`app/api/webhooks.py`** — webhook token validation switched to `secrets.compare_digest()` to eliminate timing side-channel

Deferred (LAN-compensated or feature-gated): SSRF guards in scrape tool, TrustedHostMiddleware, webhook body-size/rate caps, pending token replay telemetry, prompt-injection content tagging.

---

## [0.7.0] - 2026-03-08

### Added

#### Slash command layer

- **`app/commands/registry.py`** — `SlashCommandContext`, `SlashCommand` ABC, `SlashCommandRegistry`; provides the contract all command handlers implement
- **`app/commands/dispatcher.py`** — `try_dispatch()` intercepts `/command` messages before the LLM is invoked; handles permission checks, emits `cmd.dispatch` events with duration and success flag
- **`app/commands/handlers.py`** — six built-in commands registered at import time:
  - `/help` — lists all commands visible to the caller (admin commands hidden from non-admins)
  - `/contextstats` — assembles and measures the full context (messages, summary, profiles, memories) and returns a char/token breakdown
  - `/history [n]` — shows the last n messages (default 10, max 40) from conversation history; notes if a summary exists
  - `/schedule` — lists active reminders and scheduled Homey actions for the current user
  - `/status` *(admin)* — reports scheduler, Homey MCP, and Prometheus MCP availability
  - `/users` *(admin)* — lists household members with admin flags
- **`app/bot.py`** — intercept added after user lookup, before `assemble_context()`; unknown `/commands` return an error without touching the LLM; non-admin callers of admin commands receive a denial message

---

## [0.6.5] - 2026-03-08

### Added

#### Episodic memory lifecycle

- **Importance tiering** (`app/models/memory.py`, `alembic/versions/0002_memory_db_lifecycle.py`) — `EpisodicMemory` gains two new columns: `importance` (`critical` / `important` / `normal` / `ephemeral`, default `normal`) and `last_used_at` (nullable datetime, updated on every retrieval)
- **Near-duplicate suppression** (`app/memory/episodic.py`) — `store_memory()` runs a vector similarity check before inserting; if an existing memory in the same scope is within the configured distance threshold, the new memory is discarded and the existing one's `last_used_at` is refreshed. Threshold configurable via `MEMORY_DEDUP_DISTANCE_THRESHOLD` (default 0.15)
- **Access tracking** (`app/memory/episodic.py`) — `search_memories()` and `_recency_fallback()` write `last_used_at` on every retrieval so idle TTL reflects actual usage rather than insertion time
- **Daily memory purge** (`app/scheduler/cleanup.py`) — `purge_stale_memories()` removes memories idle beyond their tier's TTL (ephemeral: 30 d, normal: 90 d, important: 365 d; critical: never); uses `last_used_at` with `created_at` as fallback; vec table kept in sync via `_delete_from_vec()` helper
- **`forget_memory` vec cleanup** (`app/agent/tools/memory.py`) — deleting a memory via the agent tool now also removes its embedding from the sqlite-vec table
- **`store_memory` importance param** (`app/agent/tools/memory.py`) — new `importance` argument with docstring describing each tier; agent selects tier at call time
- **Auto-extraction importance** (`app/memory/extraction.py`) — extraction prompt teaches the LLM the four tiers; output changed from `list[str]` to `list[{content, importance}]`; validated and passed through to `store_memory()`
- **Admin Details tab** (`app/control/api.py`) — episodic memory table now shows Tier (colour-coded) and Last used columns; `GET /admin/memory` includes `importance` and `last_used_at` per entry

### Fixed

#### Graceful shutdown (dev and prod)

- **`start.sh`** — app runs as a tracked background job (`APP_PID`); trap sends SIGTERM to both prometheus and app, SIGKILL after 5 s; `wait $APP_PID` keeps the shell alive
- **`app/__main__.py`** — own `loop.add_signal_handler` replaces asyncio's default SIGINT handler; polling runs as a background task; SSE streams signalled before uvicorn stops, preventing force-cancel tracebacks
- **`app/channels/telegram.py`** — each PTB cleanup coroutine wrapped in its own `try/except (CancelledError, Exception)` so `_must_cancel` doesn't abort the cleanup chain
- **`app/control/api.py`** — `signal_stream_shutdown()` exits SSE generators within 1 s, allowing uvicorn to drain cleanly
- **`app/api/server.py`** — calls `signal_stream_shutdown()` at start of lifespan teardown for clean prod container shutdown

### Changed

- `app/config.py` — added `MEMORY_TTL_EPHEMERAL_DAYS` (30), `MEMORY_TTL_NORMAL_DAYS` (90), `MEMORY_TTL_IMPORTANT_DAYS` (365), `MEMORY_DEDUP_DISTANCE_THRESHOLD` (0.15)
- `docs/memory-design.md` — updated EpisodicMemory schema, tool signature, added Memory Lifecycle section

---

## [0.6.0] - 2026-03-07

### Added

#### Control plane — `/admin` dashboard

- **`app/control/events.py`** — lightweight in-process event bus. `emit(event_type, payload, run_id)` writes to an in-memory ring buffer (last 150 events) and fans out to all active SSE subscriber queues. No external dependencies.
- **`app/control/api.py`** — FastAPI router mounted at `/admin`:
  - `GET /admin` — self-contained HTML dashboard (dark theme, no build step)
  - `GET /admin/stats` — JSON: process CPU/memory/uptime (psutil), aggregate run counts, token usage by model, tool usage counts (last 500 runs from `AgentRunLog`)
  - `GET /admin/stream` — SSE live event stream; replays last 150 events to new subscribers then streams new ones with 30s heartbeat
- **Dashboard UI**: two-panel layout — sidebar with system stats + tool usage bar chart; right panel with live activity feed (`START` / `TOOL` / `DONE` / `ERR` badges, auto-scroll, clear)

#### Instrumentation

- **`app/bot.py`** — `handle_incoming_message` now emits `run.start`, `run.complete`, and `run.error` events; generates `run_id` (UUID) threaded through the entire run; writes `AgentRunLog` to `cache.db` after each successful run (was previously unimplemented)
- **`app/homey/mcp_client.py`** — `_policy_process_tool_call` emits `run.tool_call` (with timing and success/error) in real time for every Homey tool call, including confirmation-bypassed calls
- **`app/prometheus/mcp_client.py`** — added `_instrument_process_tool_call` callback (same pattern as Homey) so Prometheus tool calls also appear in the live stream
- **`app/agent/agent.py`** — `AgentDeps` has a new `run_id: str` field; `run_conversation` accepts and forwards `run_id` so tool callbacks can tag their events to the correct run

#### Dependencies

- Added `psutil` to project dependencies (used by `/admin/stats` for process metrics)

---

## [0.5.1] - 2026-03-06

### Fixed

- **Prometheus tools missing in dev mode** (`app/__main__.py`) — `_run_development()` was not calling `start_prom_mcp()`, so Prometheus MCP was only attached in production (FastAPI lifespan). Added `await start_prom_mcp()` after `await start_mcp()` so both MCPs are loaded in dev polling mode.
- **Agent re-prompts after Telegram confirmation** (`app/channels/telegram.py`) — if `direct_call_tool` raised any exception during `_execute_confirmed_action`, the success-path `save_message_pair` was never reached. On the next user message the agent saw an incomplete history and re-triggered the policy gate. Fix: `save_message_pair` is now called in both the success and failure paths with explicit messages that tell the agent the action was confirmed and either completed or failed, preventing unnecessary re-confirmation loops.

---

## [0.5.0] - 2026-03-06

### Added

#### Prometheus MCP integration

- `services/prometheus-mcp/` — standalone read-only MCP server exposing five tools:
  - `prom_query` — instant PromQL query (current values)
  - `prom_query_range` — range query returning `TimeSeries` with `datapoints` + `min/max/avg/latest` summaries; output shaped for future anomaly detection
  - `prom_list_metrics` — list metric names with optional prefix filter
  - `prom_label_values` — list label values (e.g. all `job` or `room` names)
  - `prom_series` — series metadata for anomaly baseline enumeration
- `app/prometheus/mcp_client.py` — HomeAgent-side connection: `MCPServerStreamableHTTP` with `tool_prefix="prom"`, no policy gate (read-only)
- Numeric guardrails in the MCP server: query timeout, max range window, min step, max series, max datapoints, max response size, optional metric prefix allowlist
- Optional Bearer token auth for Prometheus (env-driven, LAN setups leave empty)
- `PROMETHEUS_MCP_URL` added to HomeAgent `app/config.py` and `.env.example`
- `app/api/server.py` — Prometheus MCP started/stopped alongside Homey MCP in lifespan

---

## [0.4.0] - 2026-03-06

### Added

- `app/agent/tools/search.py` — `search_web` tool with provider adapter pattern: `SearchResult` dataclass + `SearchProvider` Protocol as the stable interface; `TavilyProvider` as the default backend (free tier, 1 000 searches/month); swap providers by implementing `SearchProvider` and adding a branch in `_get_provider()` keyed on `SEARCH_PROVIDER` in `.env`

### Fixed

- **Memory write missing** (`app/agent/tools/memory.py`) — agent had no mechanism to write to long-term memory; added `store_memory` tool with `content` and `scope` (`household`/`personal`) args; updated `prompts/instructions.md` with explicit rule to call the tool immediately rather than just saying it will remember
- **Agent verbosity** (`prompts/persona.md`) — brevity rule moved to top of persona so it's encountered before any other instruction; `prompts.py` now logs a warning when a prompt file is not found instead of silently returning empty string

---

## [0.3.0] - 2026-03-03

### Added

#### Scheduled Homey device actions

- `app/scheduler/actions.py` — `schedule_action()`: persists scheduled device action as a `Task` and registers an APScheduler `DateTrigger` job; `restore_pending_actions()` rehydrates active action tasks on startup
- `app/scheduler/jobs.py` — `execute_homey_action()` job: fires MCP tool call at scheduled time, notifies user on success/failure, marks task COMPLETED or FAILED
- `app/agent/tools/actions.py` — three Pydantic AI tools: `schedule_homey_action` (schedule a future device action), `list_scheduled_actions`, `cancel_scheduled_action`

#### Bash command runner (opt-in via `FEATURE_BASH=true`)

- `app/shell.py` — subprocess runner: argv-only (no shell), command allowlist, workspace-confined cwd, clean environment, timeout + process group kill, output truncation; hardcoded `ALWAYS_BLOCKED` set (shells, network tools, rm, sudo)
- `app/agent/tools/bash.py` — `run_bash_command` Pydantic AI tool with configurable allowlist, workspace dir, timeout, and output limits

#### Python script execution (opt-in via `FEATURE_PYTHON=true`)

- `app/agent/tools/python_exec.py` — `run_python_script` tool: writes LLM-generated code + optional helper files to a UUID temp dir, runs via shared shell runner, returns stdout/stderr + artifact list; lazy cleanup of runs older than 24 h

#### Web scraping (opt-in via `FEATURE_SCRAPE=true`)

- `app/agent/tools/scrape.py` — `scrape_web_page` tool: fetches URL with httpx, strips boilerplate tags with BeautifulSoup, returns clean text truncated to configured limit

#### Developer experience

- `start.sh` — one-liner launcher: `./start.sh dev` (uv polling), `./start.sh prod` (Docker Compose), plus `logs`, `stop`, `restart` subcommands

### Changed

- `app/config.py` — added `HOUSEHOLD_TIMEZONE` setting; `feature_bash`, `feature_python`, `feature_scrape` flags; per-tool settings (`BASH_*`, `PYTHON_*`, `SCRAPE_*`)
- `app/agent/agent.py` — tool registration is now conditional on feature flags; timezone now uses `ZoneInfo(settings.household_timezone)` for correct local time context
- `app/homey/mcp_client.py` and `home_profile.py` — `MCPServerHTTP` → `MCPServerStreamableHTTP` (pydantic-ai API change); removed `Authorization` header (local LAN app needs no auth)
- `prompts/persona.md` — date/time line made bold and explicitly authoritative to prevent model from overriding with training-data assumptions
- `prompts/instructions.md` — added scheduling, bash, Python, and scraping instruction sections
- `app/shell.py` — `python3`/`python` removed from `DEFAULT_ALLOWED`; dedicated Python tool is the correct interface

### Fixed

- Agent reported wrong year/time (defaulted to UTC, ignored household timezone) — fixed by `HOUSEHOLD_TIMEZONE` + `ZoneInfo`
- `alembic upgrade head` failed with multiple branch heads — command was already `heads` (plural) in startup code; documented in dev guide

## [0.2.0] - 2026-03-01

### Added

#### Milestone 2 — Bot is alive

- `app/__main__.py` — entry point: development (long-polling) and production (uvicorn webhook) modes
- `app/api/server.py` — FastAPI application with startup lifespan
- `app/api/health.py` — `/health` endpoint reporting DB, MCP, and scheduler component status
- `app/api/webhooks.py` — `/webhook/telegram` with secret-token validation
- `app/channels/base.py` — `Channel` abstract interface
- `app/channels/telegram.py` — `TelegramChannel`: polling + webhook modes, inline-button callback handling
- `app/channels/registry.py` — module-level active-channel singleton
- `app/bot.py` — central message dispatch: allowlist gate, user auto-create, agent run, response persistence
- `app/agent/llm_router.py` — `LLMRouter` / `TaskType`: task-aware model selection with fallback
- `app/agent/agent.py` — Pydantic AI `Agent` singleton with structured `AgentDeps`, dynamic system prompt, MCP toolset attachment

#### Milestone 3 — Memory + context

- `app/memory/profiles.py` — user and household profile CRUD
- `app/memory/episodic.py` — episodic memory store and retrieval: OpenAI embeddings → sqlite-vec vector search with recency fallback
- `app/memory/conversation.py` — rolling conversation history, summary compaction, recent message loading
- `app/agent/context.py` — `assemble_context()`: profiles, history, episodic memories, device state
- `app/agent/prompts.py` — prompt template loader with variable substitution and hot-reload cache

#### Milestone 4 — Homey integration

- `app/homey/mcp_client.py` — `MCPServerHTTP` singleton; policy gate callback intercepts every tool call; write tools trigger async state verification
- `app/homey/state_cache.py` — `DeviceSnapshot` CRUD; `update_snapshots_from_tool_calls()` parses agent messages
- `app/homey/home_profile.py` — `refresh_home_profile()`: queries MCP for zones/devices, writes household profile
- `app/homey/verify.py` — `verify_after_write()`: post-write read-back; user notified on mismatch

#### Milestone 5 — Policy gate + verify

- `app/models/users.py` — `ActionPolicy` model (tool pattern, arg conditions, impact level, confirm flag, cooldown)
- `app/policy/default_policies.py` — 7 built-in policies covering alarm, door lock, water shutoff, flow trigger, device control, and read tools
- `app/policy/gate.py` — `evaluate_policy()`: fnmatch matching; deterministic ordering; conservative fail for unknowns
- `app/policy/pending.py` — `PendingAction` CRUD with expiry
- `app/policy/seeder.py` — inserts missing default policies without overwriting user edits
- `alembic/versions/0002_users_db_action_policy.py` — migration for `actionpolicy` table

#### Milestone 6 — Scheduler + reminders

- `app/scheduler/engine.py` — APScheduler 4.x `AsyncScheduler` singleton
- `app/scheduler/jobs.py` — `send_reminder()` job
- `app/scheduler/reminders.py` — `schedule_reminder()`, `cancel_reminder()`, `restore_pending_reminders()` (startup rehydration)
- `app/scheduler/cleanup.py` — `purge_old_logs()` daily retention job
- `app/agent/tools/reminders.py` — `set_reminder`, `list_reminders`, `cancel_reminder` Pydantic AI tools

#### Milestone 7 — Production hardening

- `app/logging_setup.py` — `configure_logging()`: structlog integration; console renderer in dev, JSON in prod
- `app/bot.py` — per-user sliding-window rate limiter; skipped in development and test modes
- `Dockerfile` — single-stage uv build, non-root user
- `docker-compose.yml` — `./data` and `./prompts` volume mounts, Docker healthcheck
- `.dockerignore`

### Changed

- `app/api/health.py` — `/health` now reports `mcp` and `scheduler` component status
- `app/api/server.py` — lifespan wires logging, scheduler, cleanup jobs, and channel registry
- `app/__main__.py` — dev startup wires the same sequence as production lifespan

### Fixed

- **Confirmation callback ownership** (`app/channels/telegram.py`) — both confirm and cancel handlers verify via DB lookup that the pressing user owns the `PendingAction`; foreign tokens receive an ephemeral rejection
- **Episodic memory cross-user leak** (`app/memory/episodic.py`) — `search_memories` now scopes to household-wide memories (`user_id IS NULL`) plus the requesting user's personal memories; other household members' memories are never returned
- **Policy gate fail-open on unknown write tools** (`app/policy/gate.py`) — unrecognised write tools now require confirmation; `get_*` / `list_*` tools are still allowed; DB lookup failures are also fail-closed
- **Non-deterministic policy ordering** (`app/policy/gate.py`) — query uses `ORDER BY requires_confirm DESC, name ASC`; confirmation-required policies always win over permissive ones

---

## [0.1.0] - 2026-03-01

### Added

#### Project scaffolding

- `.gitignore` — covers Python artefacts, env files, data directories, secrets, macOS noise
- `.gitleaks.toml` — extends default gitleaks ruleset; allowlists `.env.example` and docs
- `.env.example` — full configuration reference with all variables documented and grouped by concern

#### Documentation

- `README.md` — overview, quick start, secret hygiene, user management, docs index
- `docs/architecture.md` — system diagram, data flows, storage layout, state cache table schemas, task state, rate limiting, health endpoint, graceful degradation, security considerations
- `docs/agent-design.md` — persona, system prompt structure (7 sections), prompt file system, tools table, memory extraction, LLM routing, guardrails
- `docs/tech-stack.md` — full dependency list with rationale; LLM router models and token limits; feature flags reference
- `docs/memory-design.md` — four memory layers (structural profiles, episodic, conversation history, working memory), retrieval logic, compaction strategy
- `docs/policy-gate.md` — declarative confirmation middleware: flow, policy table schema, default policies, impact levels, cooldowns, admin management
- `docs/graceful-degradation.md` — per-component failure contracts, degradation matrix, startup sequence, admin alerting
- `docs/observability.md` — structlog JSON format, log levels, trace IDs, `/health` endpoint schema, admin `/status` command, cost visibility and weekly summary
- `docs/development.md` — local setup, environment modes, Alembic migration workflow, unit/integration testing strategy with mock patterns, project structure tree, backup approach, code style
- `docs/integrations/telegram.md` — webhook vs polling, BotFather setup, access control (ID allowlist), onboarding flow
- `docs/integrations/homey-mcp.md` — token scopes, MCP connection via Pydantic AI, confirmation pointing to policy gate

#### Prompt templates

- `prompts/persona.md` — agent identity, tone, and communication style; supports `{agent_name}`, `{household_name}`, `{current_date}`, `{current_time}`, `{timezone}` template variables
- `prompts/instructions.md` — specific behavioural rules for home control, reminders, privacy, language scope
- `prompts/home_context.md` — home layout, device naming conventions, routines; supports `{timestamp}` and `{device_states}` for live state injection

#### Key design decisions recorded

- Single agent with rich context assembly (not multi-agent routing)
- Pydantic AI as agent framework (native MCP, multi-model, well-typed)
- Async two-webhook confirmation pattern (pending-state, not blocking)
- Telegram user ID allowlist as the sole access gate (IDs unforgeable via webhook model)
- SQLite (WAL mode) + Alembic for all structured storage; Chroma embedded for vector search
- LLM Router: task-type model selection (Sonnet primary, Haiku background, GPT-4o fallback) with feature flags
- Policy Gate: declarative SQLite-backed middleware for high-impact action confirmation
- Action Verification: read-back after Homey writes; retry once; report mismatch to user
- State cache: `device_snapshot`, `event_log`, `agent_run_log`, `pending_action` tables
- Competing action detection via `agent_run_log` with configurable time window
- Explicit multi-step task state table (ACTIVE | AWAITING_INPUT | AWAITING_CONFIRMATION | COMPLETED | FAILED | CANCELLED)
- Prompt files: editable markdown templates in `prompts/`, hot-reloadable via admin `/reload`

---

<!-- New entries go above this line -->

[0.7.1]: https://github.com/your-org/homeAgent/releases/tag/v0.7.1
[0.7.0]: https://github.com/your-org/homeAgent/releases/tag/v0.7.0
[0.6.5]: https://github.com/your-org/homeAgent/releases/tag/v0.6.5
[0.6.0]: https://github.com/your-org/homeAgent/releases/tag/v0.6.0
[0.5.1]: https://github.com/your-org/homeAgent/releases/tag/v0.5.1
[0.5.0]: https://github.com/your-org/homeAgent/releases/tag/v0.5.0
[0.4.0]: https://github.com/your-org/homeAgent/releases/tag/v0.4.0
[0.3.0]: https://github.com/your-org/homeAgent/releases/tag/v0.3.0
[0.2.0]: https://github.com/your-org/homeAgent/releases/tag/v0.2.0
[0.1.0]: https://github.com/your-org/homeAgent/releases/tag/v0.1.0
