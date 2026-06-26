# Agent Working Guide

This file is shared guidance for Codex, Claude, and human-directed coding
agents working in this repository. Keep it compact and high-signal.

## Project Identity

HomeAgent is a locally orchestrated household AI agent.

Core runtime principle:

- one conversational agent
- rich assembled context
- many tools
- policy-gated side effects

Do not redesign it into a multi-agent runtime unless the human explicitly asks.
Prefer extending the existing single-agent runtime, context assembly, world
model, task orchestration, scheduled behavior, and control loop.

## Tech Stack

Use and preserve the current stack:

- Python 3.12+
- FastAPI
- PydanticAI
- SQLModel + Alembic
- SQLite (`users.db`, `memory.db`, `cache.db`)
- `sqlite-vec` for episodic memory retrieval
- APScheduler
- Telegram via `python-telegram-bot`
- `uv` for dependency management and commands
- `ruff`, `mypy`, `pytest`

Do not introduce LangChain, LangGraph, Redis, PostgreSQL, or a new orchestration
framework unless explicitly requested.

## Sources Of Truth

When facts conflict, trust sources in this order:

1. Current code
2. `pyproject.toml`
3. Current docs in `docs/`
4. Older design prose
5. Changelog history

Use `CHANGELOG.md` to understand when a feature landed, but verify current
runtime behavior in code before editing.

## Common Runtime Areas

- `app/agent/` - agent definition, runner, context assembly, skills, LLM routing
- `app/agent/tools/` - tools exposed to the agent
- `app/tasks/` - task orchestration and active task context
- `app/memory/` - profiles, episodic memory, summaries, extraction
- `app/world/` - household world model behavior
- `app/scheduler/` - reminders, scheduled prompts, task resumes, cleanup jobs
- `app/channels/telegram.py` - Telegram transport
- `app/bot.py` - inbound message handling
- `app/control/` - admin APIs, dashboard, events, dispatcher, control loop view
- `app/email/` - AgentMail intake, confirmation, retry, preprocessing
- `app/flights/` - flight watch records, providers, polling, notifications
- `app/wine/` - wine cellar sync and inventory domain logic
- `services/*-mcp/` - standalone MCP sidecar services
- `alembic/versions/` - database migrations
- `prompts/` - runtime prompt text loaded by the agent

## Data Placement Rules

Store information in the right layer:

- profiles: small always-needed user or household facts
- world model: canonical household entities, aliases, places, devices, calendars,
  routines, relationships, and durable structured facts
- episodic memory: softer observations, preferences, and situational facts
- conversation history: recent continuity only
- tasks: resumable work, follow-ups, goals, and state transitions
- `cache.db`: operational state, logs, pending confirmations, snapshots, queues

Do not put task progress in episodic memory, canonical household structure in
episodic memory when the world model fits, or transient live device state in
memory.

## Editing Rules

- Read the smallest set of files needed.
- Prefer `rg` and targeted file reads over broad scans.
- Preserve existing patterns unless there is a clear reason to change them.
- Keep changes local to the requested subsystem.
- If a SQLModel schema changes, add or update an Alembic migration.
- If runtime behavior changes, update the most relevant doc.
- If setup or user-visible behavior changes, review `README.md` and `.env.example`.
- If prompt behavior changes, keep files in `prompts/` compact.
- Avoid unrelated refactors while implementing a feature or fix.

## Verification

Use the smallest relevant check first. Before a commit, run the CI-equivalent
gate unless the human explicitly scopes verification differently:

```bash
just check-ci
```

If `just` is not installed, run the commands from `justfile` directly:

```bash
uv run ruff check app/
uv run ruff format --check app/
uv run pytest tests/unit/ -v --tb=short
```

Useful commands:

```bash
just check-fast
just check-types
just test-unit
uv run pytest tests/unit/test_formatter.py -v
uv run alembic upgrade head
APP_ENV=development uv run python -m app
```

Mypy remediation is ongoing. Use `docs/mypy-remediation.md` as the execution
plan, and do not add `mypy` to CI until `uv run mypy app/` is clean.

## Multi-Agent Practice

Multiple coding agents may work in this repository at the same time under human
coordination. Keep coordination lightweight:

- state the subsystem and files you are touching before edits
- avoid overlapping edits unless the human is explicitly coordinating them
- share concise findings, not raw file dumps
- keep one agent on the critical path for a given change
- run focused verification and report exactly what passed or failed

Good bounded tasks include one subsystem, one mypy phase, one test gap, or one
doc/code synchronization pass. Avoid open-ended "fix everything" tasks.

## Docs And Briefs

Start with `docs/briefs/briefs.md` when orienting to a subsystem. Briefs point
to the current runtime files and deeper design docs without requiring agents to
load every long design document.

Status blocks at the top of design docs indicate whether the document describes
current behavior, a partial implementation, or a proposal. Code remains the final
source of truth.
