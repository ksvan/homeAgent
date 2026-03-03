# Development Guide

Local setup, testing strategy, mock patterns, and database migrations.

---

## Local Setup

### Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) (`curl -LsSf https://astral.sh/uv/install.sh | sh`)
- Docker Desktop (for running the full stack)
- A Telegram bot token (from @BotFather)

### First run

```bash
git clone <repo> homeAgent && cd homeAgent
cp .env.example .env
# Edit .env — minimum required: ANTHROPIC_API_KEY, TELEGRAM_BOT_TOKEN, ALLOWED_TELEGRAM_IDS

uv sync                          # install all dependencies
uv run alembic upgrade heads     # create DB schema (note: heads, plural)
./start.sh dev                   # start in polling mode
```

> **Tip:** `start.sh` is a convenience wrapper. In dev mode it runs `uv run python -m app`.
> In production it wraps `docker compose up --build -d`.

In `development` mode the bot uses Telegram long polling — no public URL or webhook setup required.

---

## Environment Modes

| `APP_ENV` | LLM calls | Telegram | Homey | Rate limit |
| --- | --- | --- | --- | --- |
| `development` | Live (real cost) | Polling | Live (use test device) | Disabled |
| `test` | Mocked | Mocked | Mocked | Disabled |
| `production` | Live | Webhook | Live | Enabled |

---

## Database Migrations (Alembic)

Schema changes are managed via Alembic migrations. Every schema change must have a migration file — never modify the DB directly.

### Common commands

```bash
# Apply all pending migrations (run on every deploy)
uv run alembic upgrade head

# Create a new migration after changing a SQLModel model
uv run alembic revision --autogenerate -m "add task state table"

# Check current migration state
uv run alembic current

# Roll back one migration
uv run alembic downgrade -1
```

### Migration files location

```text
alembic/
├── env.py
├── script.py.mako
└── versions/
    ├── 0001_initial_schema.py
    ├── 0002_add_task_state.py
    └── ...
```

### On container startup

The Docker entrypoint runs `alembic upgrade head` before starting the app. This means every deploy automatically applies pending migrations. Migrations must be backwards-compatible — never drop a column in the same migration that removes its usage from the code.

---

## Testing Strategy

### Unit tests (no external services)

Unit tests run entirely with mocked dependencies. They are fast, offline, and cover business logic.

```bash
uv run pytest tests/unit/
```

**What to mock:**

- All LLM calls → `MockLLMProvider` returns deterministic responses
- Homey MCP → `MockHomeyMCP` returns configurable device states
- SQLite → in-memory SQLite (`:memory:`)
- Chroma → `MockChromaClient` with simple dict-based storage
- APScheduler → `MockScheduler` that records scheduled jobs without firing them

Mocks live in `tests/mocks/`. All mocks implement the same interface as the real implementations — swappable by setting `APP_ENV=test`.

**Example patterns:**

```python
# Inject a fixed LLM response
mock_llm.set_response("Turn on the living room lights", "Done, lights are on.")

# Simulate Homey device state
mock_homey.set_device_state("living-room-light", "onoff", False)

# Assert a tool was called
assert mock_homey.calls == [("set_capability", "living-room-light", "onoff", True)]
```

### Integration tests (live services, safe device only)

Integration tests run against real Anthropic/OpenAI APIs and a real Homey instance, but are constrained to a single designated "safe" test device that is inconsequential to the household (e.g. a spare lamp in an office, not the main living area).

```bash
APP_ENV=development uv run pytest tests/integration/ -v
```

**Safety constraints enforced by test fixtures:**

- All Homey write operations are restricted to `HOMEY_TEST_DEVICE_ID` — attempts to write to any other device raise an error
- Tests that change device state restore it to original state in teardown (try/finally)
- Rate limiting and policy gate are disabled in test mode
- A maximum of 10 LLM calls per test run is enforced to control cost

**Configure the test device:**

```env
# .env (development/test only — never in production .env)
HOMEY_TEST_DEVICE_ID=your-safe-test-device-id
INTEGRATION_TEST_MAX_LLM_CALLS=10
```

### Running all tests

```bash
# Unit only (fast, no credentials needed)
uv run pytest tests/unit/

# Unit + integration (requires .env with real credentials)
uv run pytest tests/

# With coverage
uv run pytest --cov=app --cov-report=html tests/unit/
```

---

## Project Structure

```text
homeAgent/
├── app/
│   ├── __main__.py             # Entry point
│   ├── agent/
│   │   ├── agent.py            # Pydantic AI agent definition
│   │   ├── context.py          # Context assembly (profiles + memories)
│   │   ├── llm_router.py       # LLMRouter class
│   │   ├── policy_gate/
│   │   │   ├── gate.py         # PolicyGate middleware
│   │   │   └── default_policies.py
│   │   └── tools/
│   │       ├── actions.py      # Scheduled device actions
│   │       ├── bash.py         # Bash command runner tool
│   │       ├── memory.py       # store_memory tool
│   │       ├── python_exec.py  # Python script execution tool
│   │       ├── reminders.py    # Reminder tools
│   │       └── scrape.py       # Web scraping tool
│   ├── channels/
│   │   ├── base.py             # Channel abstract interface
│   │   └── telegram.py         # TelegramChannel implementation
│   ├── memory/
│   │   ├── profiles.py         # User + household profiles
│   │   ├── episodic.py         # Episodic memory (Chroma)
│   │   └── conversation.py     # Conversation history + compaction
│   ├── models/                 # SQLModel database models
│   │   ├── users.py
│   │   ├── memory.py
│   │   ├── cache.py
│   │   └── tasks.py
│   ├── api/
│   │   ├── server.py           # FastAPI app + middleware
│   │   ├── webhooks.py         # /webhook/telegram, /webhook/homey
│   │   └── health.py           # /health endpoint
│   ├── scheduler/
│   │   ├── actions.py          # Scheduled Homey action logic + startup restore
│   │   ├── cleanup.py          # Log retention jobs
│   │   ├── engine.py           # APScheduler singleton
│   │   ├── jobs.py             # Job definitions (reminders, device actions)
│   │   └── reminders.py        # Reminder schedule/cancel/restore
│   ├── shell.py                # Subprocess runner (bash + python tools)
│   └── config.py               # Settings + FeatureFlags from .env
├── alembic/                    # DB migration files
├── tests/
│   ├── unit/
│   ├── integration/
│   └── mocks/
├── prompts/                    # Editable system prompt templates
│   ├── persona.md              # Agent identity and tone
│   ├── instructions.md         # Behavioural rules
│   └── home_context.md         # Home layout and device context
├── docs/
├── docker/
│   └── Dockerfile
├── docker-compose.yml
├── pyproject.toml
├── start.sh                    # Dev/prod launcher
└── .env.example
```

---

## Data Backup

The `data/` directory contains all persistent state: SQLite databases and the Chroma vector store. It is mounted as a Docker volume and is gitignored.

**Simple backup approach** — run this on the host machine (not inside the container):

```bash
# One-off backup
tar -czf homeagent-backup-$(date +%Y%m%d).tar.gz ./data/

# Automated nightly backup via cron (add to host crontab)
0 2 * * * cd /path/to/homeAgent && tar -czf ~/backups/homeagent-$(date +\%Y\%m\%d).tar.gz ./data/ 2>&1 | logger -t homeagent-backup
```

**Restore:**

```bash
docker compose down
tar -xzf homeagent-backup-20260301.tar.gz
docker compose up -d
```

Keep at least 7 days of backups. The `data/` directory is typically 10–50 MB for years of household use.

---

## Code Style

```bash
# Format + lint
uv run ruff check --fix .
uv run ruff format .

# Type check
uv run mypy app/
```

These are enforced in CI. Run them before committing.
