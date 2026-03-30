# Architecture

## Overview

HomeAgent is a locally orchestrated household AI service. It runs continuously on one machine, serves multiple household members through messaging channels, and integrates with Homey, Prometheus, and internal MCP-backed tools.

The core design principle is still:

**one agent, rich context, many tools**

But the current runtime is no longer only "profiles + fuzzy memory". It now includes a structured **household world model** that grounds people, places, devices, calendars, routines, and durable facts in canonical entities.

Visual diagrams: [architecture-diagrams.md](architecture-diagrams.md)

---

## System Diagram

```text
┌──────────────────────────────────────────────────────────────┐
│                        Channels                              │
│   [Telegram]   [WhatsApp*]   [Web UI*]   [Voice*]           │
└─────────────────────────┬────────────────────────────────────┘
                          │ messages / callbacks
┌─────────────────────────▼────────────────────────────────────┐
│                   FastAPI Server                             │
│   /webhook/telegram    /health    /admin/*                  │
└─────────────────────────┬────────────────────────────────────┘
                          │
┌─────────────────────────▼────────────────────────────────────┐
│                  Agent Orchestrator                          │
│                                                              │
│   1. Identify user + household                               │
│   2. Slash-command intercept                                 │
│   3. Assemble context                                        │
│      - profiles                                              │
│      - world model                                           │
│      - conversation summary                                  │
│      - episodic memories                                     │
│      - recent message history                                │
│   4. LLM call via LLM Router                                 │
│   5. Policy Gate on high-impact actions                      │
│   6. Execute tool calls                                      │
│   7. Persist turn + logs + snapshots                         │
│   8. Background memory extraction / summarization            │
└──────┬──────────────────┬──────────────────┬────────────────┘
       │                  │                  │
┌──────▼──────┐  ┌────────▼───────┐  ┌──────▼────────────────────┐
│  LLM Router │  │   Tool Layer   │  │  Storage + Runtime State  │
│             │  │                │  │                           │
│ Claude      │  │ Homey MCP      │  │ users.db                  │
│ (primary)   │  │ Prometheus MCP │  │ - households, users       │
│             │  │ Tools MCP      │  │ - calendars, tasks        │
│ GPT-4o      │  │ reminders      │  │ - scheduled prompts       │
│ (fallback)  │  │ sched. actions │  │ - action policies         │
│             │  │ memory tools   │  │ - world model entities    │
│ Haiku/Mini  │  │ world-model    │  │                           │
│ (background)│  │ tools          │  │ memory.db                 │
│             │  │                │  │ - profiles                │
└─────────────┘  └────────────────┘  │ - episodic memories       │
                                     │ - sqlite-vec index        │
                                     │ - conversation history    │
                                     │ - conversation summaries  │
                                     │                           │
                                     │ cache.db                  │
                                     │ - device snapshots        │
                                     │ - event log               │
                                     │ - agent run log           │
                                     │ - pending confirmations   │
                                     └──────────┬────────────────┘
                                                │
                                      ┌─────────▼─────────┐
                                      │    Scheduler      │
                                      │    APScheduler    │
                                      │ - reminders       │
                                      │ - scheduled       │
                                      │   Homey actions   │
                                      │ - scheduled       │
                                      │   prompts         │
                                      │ - cleanup jobs    │
                                      └───────────────────┘

* Planned, not yet implemented
```

---

## Core Design Decisions

### Single agent with typed household grounding

HomeAgent keeps one conversational agent for the whole household domain rather than routing requests between specialist sub-agents. What changed with the world-model work is not the number of agents, but the quality of the grounding available to that one agent.

The agent now reasons over:

- persistent user profile facts
- persistent household profile facts
- a compact structured household model
- episodic semantic memory
- recent conversation turns

That keeps the conversational surface simple while making tool use and disambiguation more reliable.

### Context assembly is the real orchestrator

The key runtime work happens before each LLM call. `assemble_context()` currently builds:

1. User profile text from `memory.db`
2. Household profile text from `memory.db`
3. A formatted `## Household Model` snapshot from `users.db`
4. Recent conversation turns from `memory.db`
5. An optional rolling conversation summary
6. Relevant episodic memories retrieved from sqlite-vec

The conversation agent then adds:

- prompt files (`persona.md`, `instructions.md`)
- a machine-readable `<time_context>` block with current ISO timestamp and timezone

Current device state is still **not** preloaded into the prompt. For live state, the agent is expected to call Homey tools.

### World model as a first-class runtime layer

The world model is now a real runtime component, not just a design idea.

It lives in `users.db` and is bootstrapped on startup from trusted sources:

- `User` rows -> `HouseholdMember`
- calendar rows -> `CalendarEntity`
- Homey zones -> `Place`
- Homey devices -> `DeviceEntity`
- hardcoded seed facts -> `WorldFact`

The current schema also includes:

- `MemberInterest`
- `MemberGoal`
- `MemberActivity`
- `RoutineEntity`
- `Relationship`

The agent sees a compact formatted snapshot of this model on every run, and also has explicit tools to read and update it conservatively.

### Channel abstraction

All user-facing transport is behind a channel interface. The agent core does not know whether it is replying via Telegram or another future channel.

```text
Channel (abstract)
├── send_message(user_id: str, text: str) -> None
├── send_confirmation_prompt(user_id, action_description, token) -> None
├── get_user_from_event(event) -> User
└── parse_incoming(raw) -> Message

TelegramChannel(Channel)     <- implemented
WhatsAppChannel(Channel)     <- future
WebChannel(Channel)          <- future
```

### Slash commands stay outside the LLM path

Slash commands are intercepted in `app/bot.py` before context assembly and before the model is called. This keeps low-cost operational tasks deterministic and cheap.

Current built-ins include:

- `/help`
- `/contextstats`
- `/history`
- `/schedule`
- `/prompts`
- `/status`
- `/users`

### LLM Router with per-slot provider binding

`LLMRouter` still chooses models by task type, but provider binding is now per slot rather than implied globally. Each model slot can use its own API key and therefore its own provider.

This avoids silent mismatches like "OpenAI model name with Anthropic key" or the reverse.

### Policy Gate

The Policy Gate remains the safety boundary between "the model wants to do something" and "the side effect is executed".

- read-only tools usually pass through
- unknown write-capable tools default to confirmation
- Homey writes can trigger async user confirmation
- scheduled unattended Homey actions are checked at schedule time so high-impact actions cannot be queued for later execution

See [policy-gate.md](policy-gate.md) for the full design.

### Post-write verification is best-effort

After Homey write actions, the runtime schedules a fire-and-forget verification read-back. The current implementation:

1. waits a short configured delay
2. calls Homey `get_device_state`
3. updates the device snapshot cache with what Homey reported
4. warns the user only if the verification read-back itself fails

It does **not** yet perform strong semantic expected-vs-actual matching for every device type.

### Unified scheduled task persistence

`Task` rows in `users.db` are currently used as durable records for:

- reminders
- scheduled Homey actions

The schema is still broad enough for future multi-step conversational task orchestration, but that broader task-resume flow is not yet wired into runtime context assembly.

Detailed proposal: [multi-step-task-design.md](multi-step-task-design.md)

---

## Confirmation Flow

Confirmations are asynchronous. The webhook handling the original message does not wait for a button press.

```text
Webhook 1:
  agent proposes tool call
  -> policy gate requires confirmation
  -> save PendingAction in cache.db
  -> send inline Yes/No prompt
  -> return response

Webhook 2:
  user presses button
  -> callback handled
  -> pending action validated and deleted
  -> tool executed or cancelled
  -> user notified
```

The `pending_action` table lives in `cache.db`:

```text
pending_action
├── token          UUID primary key
├── household_id
├── user_id
├── tool_name
├── tool_args      JSON
├── policy_name
├── created_at
└── expires_at
```

---

## Data Flow: Incoming Message

```text
1. Telegram sends POST to /webhook/telegram
2. FastAPI validates the secret token header
3. Telegram user ID checked against ALLOWED_TELEGRAM_IDS
4. Existing user loaded or placeholder user auto-created
5. If message starts with /:
     -> dispatch slash command
     -> return command response without LLM
6. Otherwise assemble context:
     - profiles
     - world model
     - conversation summary
     - episodic memories
     - recent conversation turns
7. Run conversation agent with MCP + built-in tools
8. Policy gate intercepts sensitive tool calls when needed
9. Final response returned to user
10. Persist:
      - text pair for summarization
      - full conversation turn for model history
      - agent run log
      - device snapshots from Homey tool calls
11. Background:
      - episodic memory extraction
      - conversation summarization when thresholds are exceeded
```

## Data Flow: Scheduled Prompt

```text
1. APScheduler fires a scheduled prompt job
2. The saved prompt is looked up in users.db
3. A synthetic conversation run is executed through the same agent pipeline
4. The response is delivered to the target channel user
5. Run events and persistence behave like a normal conversation turn
```

## Data Flow: Startup

```text
1. FastAPI lifespan starts
2. Seed action policies
3. Start Homey, Prometheus, and Tools MCP connections
4. Reload the agent singleton so connected MCP toolsets are attached
5. Start APScheduler
6. Restore pending reminders, actions, and scheduled prompts
7. Register cleanup jobs
8. Fire-and-forget startup syncs:
     - refresh_home_profile()
     - bootstrap_world_model()
9. Initialize Telegram channel
```

---

## Storage Layout

```text
data/
└── db/
    ├── users.db      # users, households, calendars, tasks, scheduled prompts,
    │                 # action policies, world model entities
    ├── memory.db     # profiles, episodic memories, conversation turns/messages,
    │                 # conversation summaries, sqlite-vec virtual table
    └── cache.db      # device snapshots, event log, agent run log, pending_action
```

All structured storage is SQLite in WAL mode. Semantic retrieval uses `sqlite-vec` inside `memory.db`; there is no separate vector database service.

---

## Key Tables

### `users.db`

```text
household
user
channel_mapping
calendar
task
scheduledprompt
action_policy
householdmember
memberinterest
membergoal
memberactivity
place
deviceentity
calendarentity
routineentity
relationship
worldfact
```

### `memory.db`

```text
userprofile
householdprofile
episodicmemory
conversationmessage
conversationturn
conversationsummary
episodic_memory_vec   # sqlite-vec virtual table
```

### `cache.db`

```text
devicesnapshot
eventlog
agentrunlog
pendingaction
```

---

## Conversation Memory Model

HomeAgent now has three distinct durable knowledge layers plus the assembled runtime view:

1. Profiles
2. Structured household world model
3. Episodic memory
4. Conversation history / summary

Important current behavior:

- recent context comes from `ConversationTurn`, not just raw user/assistant text
- only the newest 3 turns keep full tool-return payloads
- older turns are retained with tool-return content replaced by `[result omitted]`
- rolling summarization starts once text-message history exceeds 20 messages

See [memory-design.md](memory-design.md) for the detailed design.

---

## Admin Dashboard

The admin dashboard at `/admin` is the control plane for the running service.

Current capabilities include:

- live SSE event feed for runs, jobs, memory events, commands, and world-model updates
- operational stats and status
- scheduler inspection
- a dedicated **World Model** view backed by `GET /admin/world-model`
- authenticated write endpoints for world facts, routines, aliases, member details, and entity deletion

The world-model admin endpoints are served from the same FastAPI app; there is no separate admin service.

---

## Health and Degradation

`GET /health` reports:

- `db_users`
- `db_memory`
- `db_cache`
- `mcp_homey`
- `mcp_prom`
- `mcp_tools`
- `scheduler`

The service is considered degraded when one or more components are unavailable but the process is still functioning.

See [observability.md](observability.md) and [graceful-degradation.md](graceful-degradation.md).

---

## Security Considerations

- Telegram webhooks are validated with a secret token header.
- Only allowlisted Telegram IDs are accepted.
- High-impact tool calls can be gated behind explicit confirmation.
- Pending confirmations are scoped to the requesting user and expire automatically.
- World-model writes are conservative and inspectable through admin endpoints and the event feed.
- Conversation content may be sent to external LLM providers depending on configured models.

---

## Deployment

The repo runs as a Docker Compose stack, not a single process in a single container.

Typical runtime services are:

- `homeagent`
- `tools`
- `prometheus-mcp`
- `cloudflared`

See [README.md](../README.md) for build and run instructions.
See [frameworks-and-services.md](frameworks-and-services.md) for the technology inventory.
