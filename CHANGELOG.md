# Changelog

All notable changes to HomeAgent are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning follows [Semantic Versioning](https://semver.org/).

---

## Unreleased

### Planned

- Channels: email, iMessage, voice
- TTS via Homey (cast to Google Nest etc.)
- Home awareness / anomaly detection (Prometheus baseline jobs)
- Improved memory: associate scenarios (e.g. "goodnight") with device action sets

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

[0.5.1]: https://github.com/your-org/homeAgent/releases/tag/v0.5.1
[0.5.0]: https://github.com/your-org/homeAgent/releases/tag/v0.5.0
[0.4.0]: https://github.com/your-org/homeAgent/releases/tag/v0.4.0
[0.3.0]: https://github.com/your-org/homeAgent/releases/tag/v0.3.0
[0.2.0]: https://github.com/your-org/homeAgent/releases/tag/v0.2.0
[0.1.0]: https://github.com/your-org/homeAgent/releases/tag/v0.1.0
