# HomeAgent

A locally-orchestrated personal AI agent for your household. Talks to your family via Telegram (and other channels), controls your smart home via Homey, remembers preferences over time, and handles everyday personal assistant tasks.

Runs 24/7 in Docker on a Mac or Linux machine. Uses cloud LLMs (Claude, GPT-4o) for reasoning — conversations are sent to Anthropic/OpenAI APIs. All stored data (conversation history, memories, device state) stays local on your machine.

Developed by Claude, with assistance from me and Codex.

---

## What It Does

- **Chat naturally** — talk to it like any LLM, through Telegram
- **Control your home** — "turn off the living room lights", "set the thermostat to 21 degrees"
- **Remember your family** — learns preferences, routines, and context over time
- **Household world model** — maintains structured knowledge about members, places, devices, routines, and facts
- **Multi-step tasks** — plans, tracks, and resumes work across multiple conversation turns
- **Cross-user features** — ask it to remind a family member about something
- **Personal assistant** — find restaurants, answer questions, set reminders
- **Event-driven** — reacts to home events, runs scheduled tasks

---

## Architecture Overview

See [docs/architecture.md](docs/architecture.md) for the full design.

```
[Telegram]  [WhatsApp*]  [Events]  [Cron]
      └──────────┬────────────┘
           [FastAPI Server]
                 │
         [Agent Orchestrator]
           (Pydantic AI)
                 │
      ┌──────────┼──────────┐
   [Claude]  [GPT-4o]   [Tools]
                           │
                    ┌──────┴──────┐
               [Homey MCP]   [Web / Other]
```

*Future channel

---

## Prerequisites

- Docker and Docker Compose
- Anthropic API key
- OpenAI API key
- Telegram Bot token (from @BotFather)
- Homey Personal Access Token

---

## Quick Start

### 1. Clone and configure

```bash
git clone <repo-url> homeAgent
cd homeAgent
cp .env.example .env
# Edit .env with your API keys and tokens
```

### 2. Build the image

```bash
docker compose build
```

### 3. Run

```bash
docker compose up -d
```

### 4. Set up Telegram webhook

```bash
curl -X POST "https://api.telegram.org/bot<YOUR_TOKEN>/setWebhook" \
  -H "Content-Type: application/json" \
  -d '{"url": "https://your-domain.com/webhook/telegram", "secret_token": "<YOUR_WEBHOOK_SECRET>"}'
```

See [docs/integrations/telegram.md](docs/integrations/telegram.md) for full setup including exposing your local server.

---

## Configuration

All configuration is via `.env`. See `.env.example` for all available options with descriptions.

Key settings:

| Variable | Description |
|---|---|
| `ANTHROPIC_API_KEY` | Claude API key (primary LLM) |
| `OPENAI_API_KEY` | OpenAI key (fallback + embeddings) |
| `TELEGRAM_BOT_TOKEN` | From @BotFather |
| `TELEGRAM_WEBHOOK_URL` | Public HTTPS URL for your server |
| `HOMEY_TOKEN` | Homey Personal Access Token |
| `HOMEY_HOME_ID` | Your Homey home ID |
| `ALLOWED_TELEGRAM_IDS` | Comma-separated list of permitted Telegram user IDs |
| `ADMIN_TELEGRAM_IDS` | Subset of above with admin privileges |
| `APP_ENV` | `development` or `production` |

---

## Secret Hygiene

**Critical rules — read before pushing to any git remote:**

- `.env` is gitignored. **Never remove it from `.gitignore`.** Never commit it.
- `.env.example` is committed and must contain only placeholder values (`sk-ant-...`, `123456789`, etc.). Never put real credentials in it. This is just a template, helpful samples to get going
- `data/` is gitignored. It contains conversation history and personal memories.
- `uv.lock` **should** be committed — it ensures reproducible builds.

**Verify nothing sensitive is staged before every commit:**

```bash
git diff --cached   # review everything staged
```

**Scan for accidentally committed secrets:**

```bash
# Install gitleaks: https://github.com/gitleaks/gitleaks
gitleaks detect --config .gitleaks.toml
```

**If you accidentally commit a secret:**

1. Rotate the credential immediately (don't wait)
2. Remove it from git history: `git filter-repo` or BFG Repo Cleaner
3. Force-push to overwrite remote history
4. Assume the secret is compromised regardless

---

## User Management

Access is controlled by `ALLOWED_TELEGRAM_IDS` in `.env`. Only listed Telegram user IDs can interact with the bot — all others are silently ignored.

To find a Telegram user ID: message `@userinfobot` on Telegram.

User commands:

- `/help` — list available commands
- `/contextstats` — show context size breakdown for the next LLM call
- `/history [n]` — show recent conversation history (default 10 messages)
- `/schedule` — list active reminders and scheduled Homey actions

Admin commands:

- `/status` — operational status (scheduler, Homey MCP, Prometheus MCP)
- `/users` — list household members with admin flags

---

## Homey Event Webhook

HomeAgent can receive events pushed from Homey Advanced Flows and react to them autonomously — for example, alerting when motion is detected after midnight, or notifying when a door is left open.

### 1. Configure a shared secret

Add to `.env`:

```env
HOMEY_WEBHOOK_SECRET=<generate with: python -c "import secrets; print(secrets.token_hex(32))">
```

### 2. Create an EventRule

Insert a row directly into `data/db/users.db` (or via the admin API once a UI is added):

```sql
INSERT INTO eventrule (
  id, household_id, user_id, channel_user_id,
  name, source, event_type, entity_id, capability,
  value_filter_json, condition_json, cooldown_minutes,
  prompt_template, enabled, created_at, updated_at
) VALUES (
  hex(randomblob(16)),
  '<your-household-id>',
  '<your-user-id>',
  '<your-telegram-id>',
  'Motion alert after 22:00',
  'homey',
  'device_state_change',
  '<homey-device-uuid>',
  'alarm_motion',
  '{"eq": true}',
  '{"quiet_hours_start": "07:00", "quiet_hours_end": "22:00"}',
  10,
  'Motion detected in {zone} at {time}. Is anyone expected home?',
  1,
  datetime('now'), datetime('now')
);
```

Fields:

| Field | Description |
| --- | --- |
| `event_type` | `device_state_change` or `flow_trigger` |
| `entity_id` | Homey device UUID, or `*` for any device |
| `capability` | Capability name to match (e.g. `alarm_motion`), or omit for any |
| `value_filter_json` | Optional: `{"eq": true}`, `{"gt": 22.5}`, `{"ne": null}` |
| `condition_json` | Optional: `{"quiet_hours_start": "HH:MM", "quiet_hours_end": "HH:MM"}` |
| `cooldown_minutes` | Minimum minutes between agent triggers for this rule (default 5) |
| `prompt_template` | Sent to the agent; supports `{entity_name}`, `{capability}`, `{value}`, `{zone}`, `{time}` |

### 3. Create a Homey Advanced Flow

1. Add a trigger card for the device/capability you want to react to
2. Add an action card: **HTTP request → POST**
3. URL: `http://<homeagent-lan-ip>:8080/webhook/homey/event`
4. Header: `X-Homey-Secret: <your-secret>`
5. Body (JSON):

```json
{
  "event_type": "device_state_change",
  "entity_id": "{{device.id}}",
  "entity_name": "{{device.name}}",
  "capability": "alarm_motion",
  "value": true,
  "zone": "{{device.zone.name}}"
}
```

Use Homey's flow variables (`{{device.id}}` etc.) to populate the fields dynamically.

### How it works

When a POST arrives:

1. Homey secret is validated
2. `household_id` is resolved server-side (single-household)
3. The event is enqueued on the internal event bus
4. The dispatcher checks matching `EventRule` records
5. If a rule matches and passes cooldown/quiet-hours/value-filter checks, the agent is called with a structured prompt envelope
6. The agent decides what action to take and sends a message to the configured channel

To inspect rules: `GET /admin/event-rules` (requires admin token).

---

## Integrations

- [Telegram setup guide](docs/integrations/telegram.md)
- [Homey MCP setup guide](docs/integrations/homey-mcp.md)

---

## Development

### Run locally without Docker

```bash
# Install uv
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install dependencies
uv sync

# Run in dev mode (uses polling instead of webhook)
APP_ENV=development uv run python -m app
```

### Project structure

```
homeAgent/
├── app/                    # Application source
│   ├── agent/              # Pydantic AI agent, tools, context assembly
│   ├── channels/           # Channel adapters (Telegram, future WhatsApp)
│   ├── control/            # Admin dashboard, SSE events, auth
│   ├── memory/             # Memory layers: profiles, episodic, vector
│   ├── models/             # SQLModel database models
│   ├── scheduler/          # APScheduler jobs and cron tasks
│   ├── tasks/              # Multi-step task orchestration
│   ├── world/              # Household world model
│   ├── api/                # FastAPI routes and webhook handlers
│   └── homey/              # Homey MCP client and state cache
├── services/               # Co-located service containers
│   ├── tools-mcp/          # Sandboxed bash/python/scrape/search
│   └── prometheus-mcp/     # Prometheus metrics MCP server
├── docs/                   # Design documentation
├── prompts/                # Agent persona, instructions, home context
├── data/                   # Runtime data (git-ignored)
│   ├── db/                 # SQLite databases
│   └── chroma/             # Vector store
├── docker/                 # Dockerfile and build assets
├── .env.example
├── docker-compose.yml
└── pyproject.toml
```

---

## Multi-Platform Build

The Docker image supports both ARM64 (Apple Silicon Mac) and AMD64 (Linux).

```bash
# Build for both platforms and push
docker buildx build \
  --platform linux/amd64,linux/arm64 \
  -t homeagent:latest \
  --push .
```

---

## Docs

- [Architecture](docs/architecture.md)
- [Architecture Diagrams](docs/architecture-diagrams.md)
- [Agent Design](docs/agent-design.md)
- [Memory Design](docs/memory-design.md)
- [Household World Model](docs/household-world-model-design.md)
- [Multi-Step Tasks](docs/multi-step-task-design.md)
- [Observability](docs/observability.md)
- [Slash Commands](docs/slash-commands-design.md)
- [Tech Stack](docs/tech-stack.md)
- [Telegram Integration](docs/integrations/telegram.md)
- [Homey MCP Integration](docs/integrations/homey-mcp.md)

## Other relevant

- Folder `prompts/` contains persona, instructions, and home context templates for the LLM. Update these to fit your household and preferences.
