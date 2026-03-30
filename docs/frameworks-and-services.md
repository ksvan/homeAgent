# HomeAgent — Frameworks & Services

## AI / LLM

| Name | What it does | How it's used here |
|---|---|---|
| **pydantic-ai** | Agentic AI framework with multi-model support, structured outputs, and MCP toolset integration | Core agent runtime. Defines the `Agent` singleton, wires in MCP toolsets, and orchestrates all conversations — `app/agent/agent.py` |
| **Anthropic (Claude)** | Official client for Claude API (Sonnet, Haiku) | Primary LLM for conversation and background tasks, routed via pydantic-ai |
| **OpenAI** | Official client for GPT-4o and embedding models | Optional fallback LLM (gpt-4o) and the embedding model (text-embedding-3-small) for episodic memory |

---

## Web & API

| Name | What it does | How it's used here |
|---|---|---|
| **FastAPI** | Async Python web framework with auto-validation | Main application server — hosts the Telegram webhook, admin/SSE streams, and health endpoints |
| **Uvicorn** | ASGI server for Python | Runs FastAPI in production, started via `python -m app` |
| **slowapi** | Request rate limiting middleware for FastAPI | Limits inbound requests per user to prevent abuse |

---

## Database & Storage

| Name | What it does | How it's used here |
|---|---|---|
| **SQLModel** | ORM combining SQLAlchemy 2.0 and Pydantic | Primary ORM for all models across three SQLite databases, including users, households, tasks, calendars, scheduled prompts, memories, and the household world model |
| **SQLite** (stdlib) | Embedded relational database | Three separate databases in WAL mode: `users.db`, `memory.db`, `cache.db` |
| **sqlite-vec** | SQLite extension for vector similarity search | Stores and queries 1536-dim OpenAI embeddings for semantic episodic memory search (`episodic_memory_vec` virtual table) |
| **Alembic** | Database migration tool for SQLAlchemy | Manages schema versioning across all three databases |

---

## Scheduling

| Name | What it does | How it's used here |
|---|---|---|
| **APScheduler 4.x** | Async job scheduler with date and cron triggers | Runs one-shot reminders and Homey actions (DateTrigger), recurring scheduled prompts (CronTrigger), and daily cleanup jobs. State is persisted in the DB and restored on startup |

---

## Messaging

| Name | What it does | How it's used here |
|---|---|---|
| **python-telegram-bot** | Async Telegram Bot API client | Primary user interface — receives inbound messages via webhook, sends responses, and renders inline Yes/No buttons for policy confirmation |

---

## MCP (Model Context Protocol)

| Name | What it does | How it's used here |
|---|---|---|
| **pydantic-ai MCP client** (`MCPServerStreamableHTTP`) | MCP client built into pydantic-ai for connecting to MCP servers | Connects the agent to the three MCP servers (Homey, Prometheus, Tools) and exposes their tools at runtime |
| **FastMCP** | Python framework for building MCP servers | Powers the two co-located MCP services: `services/tools-mcp` and `services/prometheus-mcp` |

---

## External Services

| Name | What it does | How it's used here |
|---|---|---|
| **Homey** (MCP) | Smart home hub — controls devices, flows, and zones | Agent calls Homey MCP for live home automation and also uses `get_home_structure` during startup to bootstrap `Place` and `DeviceEntity` rows in the world model |
| **Prometheus** (MCP) | Time-series metrics database | Read-only metrics access (power, temperature, uptime). Wrapped in `services/prometheus-mcp` to expose it as an MCP server |
| **Tavily** | Web search SaaS API | Powers the `search` tool in `services/tools-mcp`; used when the agent needs to query current facts or the web |
| **Cloudflare Tunnel** | Tunneling service for NAT traversal | Exposes the FastAPI webhook endpoint to the internet without port forwarding, enabling Telegram to reach the bot at home |

---

## HTTP & Networking

| Name | What it does | How it's used here |
|---|---|---|
| **httpx** | Async HTTP client | Used in both MCP services for outbound HTTP — Prometheus API calls and calendar ICS fetches |

---

## Configuration

| Name | What it does | How it's used here |
|---|---|---|
| **pydantic-settings** | Type-safe environment variable binding | Reads all config from `.env` with validation — API keys, model names, feature flags, token limits, storage paths |

---

## Logging & Observability

| Name | What it does | How it's used here |
|---|---|---|
| **structlog** | Structured logging with context binding | Replaces stdlib logging throughout. Emits colorized output in dev, JSON in production (Docker) |

---

## Data & Parsing

| Name | What it does | How it's used here |
|---|---|---|
| **BeautifulSoup4** | HTML parser | Backs the `scrape` tool in `services/tools-mcp` for extracting readable text from web pages |
| **recurring-ical-events** + **icalendar** | iCalendar parsing and RRULE expansion | Used in the calendar tool to parse `.ics` feeds and expand recurring events into discrete occurrences |
| **psutil** | Cross-platform process and system utilities | Available in `services/tools-mcp` for system introspection from the bash/python tool |

---

## Infrastructure

| Name | What it does | How it's used here |
|---|---|---|
| **Docker + Compose** | Container runtime and multi-container orchestration | Runtime stack for `homeagent`, `tools`, `prometheus-mcp`, and `cloudflared`; health checks and dependency ordering handled in Compose |
| **UV** | Fast Python package manager | Used in all Dockerfiles for deterministic, fast dependency installs with bytecode pre-compilation |

---

## Dev Tooling

| Name | What it does | How it's used here |
|---|---|---|
| **Ruff** | Fast Python linter | Enforces E/F/I rules at 100-char line length |
| **MyPy** | Static type checker | Strict mode enabled across the full codebase |
| **pytest + pytest-asyncio** | Test framework with async support | Runs unit and integration tests with `asyncio_mode = auto` |
