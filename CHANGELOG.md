# Changelog

All notable changes to HomeAgent are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning follows [Semantic Versioning](https://semver.org/).

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

[0.1.0]: https://github.com/your-org/homeAgent/releases/tag/v0.1.0
