# Tech Stack

All technology choices for HomeAgent, with rationale. Update this document whenever a dependency is added, removed, or upgraded.

---

## Language & Runtime

| Choice | Version | Rationale |
| --- | --- | --- |
| Python | 3.12+ | Best LLM/AI ecosystem, async support, type hints |
| uv | latest | Fast dependency management, lockfile, replaces pip+venv |

---

## Agent Framework

| Choice | Version | Rationale |
| --- | --- | --- |
| **Pydantic AI** | latest | Clean Python-first agent framework. Native MCP client support. Multi-model (Anthropic + OpenAI). Well-typed. Not over-engineered. |

Alternatives considered:

- LangGraph — more powerful but more complex; overkill for a single-agent setup
- LangChain — too much abstraction, harder to debug
- Raw API calls — too low-level, would need to reinvent tool calling

---

## LLM Router

A thin `LLMRouter` class (in `app/agent/llm_router.py`) wraps PydanticAI's model objects. It selects the appropriate model per task type, applies feature flags, enforces token limits, and handles fallback.

### Models

| Model | Provider | Role |
| --- | --- | --- |
| **claude-sonnet-4-5** | Anthropic | Primary: conversation, home control, planning |
| **claude-haiku-4-5** | Anthropic | Background: memory extraction, summarisation |
| **gpt-4o** | OpenAI | Fallback: used if Anthropic API is unavailable |
| **gpt-4o-mini** | OpenAI | Background fallback: lightweight tasks |
| **text-embedding-3-small** | OpenAI | Embeddings for semantic memory search |

All model IDs and per-task overrides are configured via `.env`.

### Token Limits (per task type)

| Task type | Max input tokens | Max output tokens |
| --- | --- | --- |
| `CONVERSATION` | 16 000 | 2 048 |
| `HOME_CONTROL` | 8 000 | 1 024 |
| `PLANNING` | 16 000 | 4 096 |
| `MEMORY_EXTRACTION` | 4 000 | 512 |
| `SUMMARIZATION` | 8 000 | 1 024 |

Limits are configurable in `.env` (e.g. `MAX_TOKENS_CONVERSATION_INPUT=16000`).

### Feature Flags

Feature flags are a `FeatureFlags` settings object loaded from `.env` at startup. No scattered `os.getenv()` calls — all flags are declared in one place.

| Flag | Default | Effect |
| --- | --- | --- |
| `FEATURE_CHEAP_BACKGROUND_MODELS` | `true` | Use Haiku/Mini for background tasks |
| `FEATURE_FALLBACK_MODEL` | `true` | Auto-fallback to secondary provider on failure |
| `FEATURE_POLICY_GATE` | `true` | Require confirmation for high-impact actions |
| `FEATURE_ACTION_VERIFY` | `true` | Verify device state after write actions |
| `FEATURE_LOCAL_MODEL` | `false` | Route background tasks to Ollama (future) |
| `FEATURE_WHATSAPP` | `false` | Enable WhatsApp channel adapter |
| `FEATURE_VOICE` | `false` | Enable voice input via Whisper (future) |
| `FEATURE_MULTI_HOME` | `false` | Enable multi-home routing (future) |

---

## API / Server

| Choice | Version | Rationale |
| --- | --- | --- |
| **FastAPI** | latest | Async, fast, excellent webhook handling, auto-docs |
| **uvicorn** | latest | ASGI server for FastAPI |
| **slowapi** | latest | Per-user rate limiting middleware for FastAPI (wraps `limits`) |

---

## Channels

| Choice | Library | Status |
| --- | --- | --- |
| Telegram | `python-telegram-bot` | Implemented |
| WhatsApp | Twilio or Meta API | Planned |

All channels implement the common `Channel` abstract interface in `app/channels/base.py`.

---

## Storage

| Choice | Version | Use |
| --- | --- | --- |
| **SQLite** (WAL mode) | (built-in) | All structured data: users, messages, profiles, reminders, state cache, task state |
| **SQLModel** | latest | ORM layer over SQLite (Pydantic + SQLAlchemy) |
| **Alembic** | latest | Database schema migrations — versioned, auto-generated from SQLModel models |
| **Chroma** | latest | Embedded vector store for semantic memory search |

SQLite runs in WAL (Write-Ahead Logging) mode to reduce write contention from concurrent webhook requests. Schema changes are managed by Alembic — never modify the DB manually. Chroma runs embedded (in-process), requiring no separate service.

Alternatives considered:

- PostgreSQL + pgvector — more powerful but adds ops complexity; unnecessary at this scale
- Redis — not needed; APScheduler handles scheduling in-process
- Pinecone / Weaviate — managed vector DBs; unnecessary complexity and cloud dependency

---

## Scheduling

| Choice | Version | Rationale |
| --- | --- | --- |
| **APScheduler** | 4.x | In-process scheduler. Handles cron jobs, intervals, one-off reminders. Persists jobs to SQLite. |

---

## Smart Home Integration

| Choice | Protocol | Rationale |
| --- | --- | --- |
| **Homey MCP** | MCP (cloud) | Official Homey integration. Exposes all devices and capabilities as MCP tools. |

See [integrations/homey-mcp.md](integrations/homey-mcp.md) for setup.

Future integrations (same MCP pattern):

- Home Assistant MCP (if added)
- Apple HomeKit (via Home Assistant bridge)

---

## Infrastructure

| Choice | Rationale |
| --- | --- |
| **Docker** | Consistent runtime across Mac and Linux |
| **Docker Compose** | Single-file service definition, volume management |
| **docker buildx** | Multi-platform builds (ARM64 + AMD64) |

### Dockerfile Strategy

Multi-stage build:

1. **Builder stage**: install `uv`, resolve and install all dependencies into a venv
2. **Runtime stage**: copy venv + app code only; no build tools; smaller image

Target image size: ~350–500 MB.

---

## Observability

| Choice | Version | Rationale |
| --- | --- | --- |
| **structlog** | latest | Structured JSON logging. Replaces standard `logging`. Context-aware (trace IDs, user IDs automatically bound to log entries). Console-pretty in dev, JSON in production. |

See [observability.md](observability.md) for logging conventions and health endpoint design.

---

## Development Tools

| Tool | Purpose |
| --- | --- |
| `ruff` | Linting + formatting (replaces black, isort, flake8) |
| `mypy` | Static type checking |
| `pytest` | Testing |
| `pytest-asyncio` | Async test support |

See [development.md](development.md) for setup, migration workflow, and testing strategy.

---

## Dependency Summary (pyproject.toml)

```toml
[project]
name = "homeagent"
version = "0.1.0"
requires-python = ">=3.12"

dependencies = [
    "pydantic-ai[mcp]",         # Agent framework with MCP support
    "fastapi",                  # API server
    "uvicorn[standard]",        # ASGI server
    "slowapi",                  # Rate limiting middleware
    "python-telegram-bot",      # Telegram channel
    "sqlmodel",                 # ORM
    "alembic",                  # DB schema migrations
    "chromadb",                 # Embedded vector store
    "apscheduler",              # Job scheduling
    "anthropic",                # Claude API client
    "openai",                   # OpenAI API client (fallback + embeddings)
    "httpx",                    # Async HTTP client
    "structlog",                # Structured JSON logging
    "pydantic-settings",        # Settings management from .env
    "python-dotenv",            # .env loading
]

[tool.uv]
dev-dependencies = [
    "ruff",
    "mypy",
    "pytest",
    "pytest-asyncio",
    "httpx",                    # Also used for test client
]
```

---

## Version Policy

- Pin major versions in `pyproject.toml`
- Use `uv lock` to generate a lockfile (`uv.lock`) that is committed to git
- Update dependencies deliberately, not automatically — test after each update
- Record significant upgrades in [CHANGELOG.md](../CHANGELOG.md)
