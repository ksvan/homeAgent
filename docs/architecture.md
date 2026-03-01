# Architecture

## Overview

HomeAgent is a locally-orchestrated AI agent server. It runs 24/7 on a single machine (Mac or Linux), serves multiple family members via messaging channels, and integrates with the home via Homey's MCP server.

The core design principle: **one agent, rich context, many tools.** Intelligence lives in context assembly and memory retrieval — not in routing between sub-agents.

---

## System Diagram

```text
┌──────────────────────────────────────────────────────────────┐
│                        Channels                              │
│   [Telegram]   [WhatsApp*]   [Web UI*]   [Voice*]           │
└─────────────────────────┬────────────────────────────────────┘
                          │ messages / events
┌─────────────────────────▼────────────────────────────────────┐
│                   FastAPI Server                             │
│   /webhook/telegram    /webhook/homey    /api/*              │
└─────────────────────────┬────────────────────────────────────┘
                          │
┌─────────────────────────▼────────────────────────────────────┐
│                  Agent Orchestrator                          │
│                                                              │
│   1. Identify user + household                               │
│   2. Assemble context (memory + state cache)                 │
│   3. LLM call via LLM Router (task-appropriate model)        │
│   4. Policy Gate — confirm high-impact actions               │
│   5. Execute tool calls + verify state change                │
│   6. Send response via channel                               │
│   7. Log run + extract new memories                          │
└──────┬──────────────────┬──────────────────┬────────────────┘
       │                  │                  │
┌──────▼──────┐  ┌────────▼───────┐  ┌──────▼──────────────┐
│  LLM Router │  │   Tool Layer   │  │   Memory + Cache    │
│             │  │                │  │                     │
│ Claude      │  │ Policy Gate ◄──┤  │ User profiles       │
│ Sonnet 4.5  │  │ Homey MCP      │  │ Home profile        │
│ (primary)   │  │ Action Verify  │  │ Conversation history│
│             │  │ Web search     │  │ Episodic memories   │
│ GPT-4o      │  │ Reminders      │  │ State cache (SQLite)│
│ (fallback)  │  │ ...extensible  │  │ Event log           │
│             │  │                │  │ Agent run log       │
│ Haiku/Mini  │  │                │  │ Vector search       │
│ (background)│  │                │  │                     │
└─────────────┘  └────────────────┘  └─────────────────────┘

                                      ┌─────────────────────┐
                                      │    Scheduler        │
                                      │ APScheduler         │
                                      │ - Cron jobs         │
                                      │ - Reminders         │
                                      │ - Cache refresh     │
                                      │ - Memory compaction │
                                      └─────────────────────┘
* Planned, not yet implemented
```

---

## Core Design Decisions

### Single Agent, Not Multi-Agent

A single conversational agent handles all tasks — home control, personal assistance, reminders, etc. This keeps the agent's context coherent across topics and makes memory simpler (one memory store, not per-agent stores).

A router pattern was considered and rejected: it adds latency, complicates memory sharing, and the routing itself becomes a failure point.

### Context Assembly as Orchestration

The "intelligence" of the orchestrator is in how it assembles context before each LLM call. Each request goes through:

1. **User identification** — who is this, what channel
2. **Profile retrieval** — user profile + household profile
3. **Active task state** — any in-progress multi-step tasks for this user
4. **Semantic memory retrieval** — relevant past memories from vector store
5. **State cache** — recent device snapshots + recent agent actions for this household
6. **Conversation history** — recent messages, summarized older history
7. **Home context** — inject current device states from cache (or live) for home-related queries
8. **Prompt assembly** — build system prompt from all above
9. **LLM call** — via LLM Router, with tools available
10. **Policy Gate** — intercept any high-impact tool calls before execution
11. **Post-response** — verify state changes, log run, update task state, extract new memories

### Channel Abstraction

All channel adapters implement a common `Channel` interface. The agent core never knows what channel it is talking through. This makes adding new channels (WhatsApp, web UI, voice) a matter of writing a new adapter, not touching agent logic.

```text
Channel (abstract)
├── send_message(user_id: str, text: str) → None
├── send_confirmation_prompt(user_id, action_description, token) → None
├── get_user_from_event(event) → User
└── parse_incoming(raw) → Message

TelegramChannel(Channel)     ← implemented
WhatsAppChannel(Channel)     ← future
WebChannel(Channel)          ← future
```

Note: confirmation is **not a blocking call** — see [Confirmation Flow](#confirmation-flow-async-pending-state) below.

### LLM Router

A thin `LLMRouter` class wraps PydanticAI's model objects. It selects the appropriate model based on task type, applies feature flag checks, and provides transparent fallback if the primary provider is unavailable.

See [agent-design.md](agent-design.md#llm-routing) for task-to-model mapping.

### Policy Gate

A declarative middleware layer that sits between "agent proposes a tool call" and "tool executes". High-impact actions are intercepted and require explicit user confirmation before proceeding.

This is configured via a policy table (not hardcoded). New policies can be added without changing agent code.

See [policy-gate.md](policy-gate.md) for full design.

### Action Verification

After every write action to Homey (or other stateful systems), a verification step queries the resulting state and compares it to the expected outcome. If the state does not match:

1. Retry the action once (after a short delay, as some updates are async)
2. If still mismatched: report the error to the user with the actual vs expected state

Verification uses the state cache as a baseline and Homey MCP for the live read-back.

### State Cache

A local SQLite layer that stores recent device snapshots, event history, and agent run logs. Purposes:

- **Reduce live API calls** — serve repeated home state queries from cache
- **Detect competing actions** — before acting on a device, check if another agent run recently touched it
- **Audit trail** — full log of what happened and why
- **Event replay** — home events can be inspected for debugging

See [State Cache Tables](#state-cache-tables) below for schema.

---

## Confirmation Flow (Async Pending-State)

Confirmations are **not blocking calls** within the webhook request. A webhook handler must return quickly; it cannot wait 60 seconds for a user to press a button. Instead, confirmations use a two-webhook pattern:

```text
Webhook 1 — agent hits a confirmation-required action:
  1. Agent proposes tool call
  2. Policy Gate: confirmation required
  3. Save PendingAction{token, tool, args, user_id, expires_at} to DB
  4. Send Telegram message with inline Yes/No buttons (token encoded in callback_data)
  5. Agent responds to user: "I need your confirmation before proceeding — see above."
  6. Webhook 1 returns

Webhook 2 — user presses Yes or No:
  1. Telegram sends callback_query POST to /webhook/telegram
  2. FastAPI routes callback_query to confirmation handler
  3. Look up PendingAction by token — check not expired, check user matches
  4a. If Yes: execute the tool, verify state, send result to user
  4b. If No: delete PendingAction, send "Cancelled" to user
  5. Webhook 2 returns
```

Expired pending actions are cleaned up by a scheduled job. If the user does not respond within `TELEGRAM_CONFIRM_TIMEOUT_SECONDS`, the action is auto-cancelled on next cleanup pass and the buttons are edited to show "Expired".

The `pending_action` table lives in `cache.db`:

```text
pending_action
├── token          (UUID, primary key — encoded in Telegram callback_data)
├── household_id
├── user_id
├── tool_name
├── tool_args      JSON
├── policy_name    which policy triggered this
├── created_at
└── expires_at
```

---

## Data Flow: Incoming Message

```text
1.  Telegram sends POST to /webhook/telegram
2.  FastAPI validates X-Telegram-Bot-Api-Secret-Token header
3.  Extract sender telegram_user_id — check against ALLOWED_TELEGRAM_IDS
    → If not in list: drop silently, return HTTP 200 (no response to sender)
4a. If callback_query: route to confirmation handler (see Confirmation Flow above)
4b. If message: TelegramChannel.parse_incoming() → Message(user_id, text, channel)
5.  Look up User record; if none: begin first-time onboarding (ask for name)
6.  Event logged to event_log
6.  Context assembled (profiles + memories + state cache + history)
7.  LLM Router selects model (task = CONVERSATION)
8.  Pydantic AI agent runs with assembled context + tools
9.  For each tool call the agent proposes:
      a. Policy Gate evaluates — confirmation required?
      b. If yes: save PendingAction, send confirmation prompt, stop tool execution
      c. If not required: execute tool, log to agent_run_log, verify state change
10. Final response returned
11. TelegramChannel.send_message() → user sees reply
12. Background: extract memories, update device snapshots
```

## Data Flow: Scheduled Task / Reminder

```text
1. APScheduler fires job at scheduled time
2. Job retrieves target user + message from DB
3. Looks up user's preferred channel
4. Logs event to event_log
5. Sends message via appropriate channel adapter
6. For complex tasks: constructs a synthetic message and runs through agent pipeline
```

## Data Flow: Home Event (future)

```text
1. Homey sends webhook to /webhook/homey
2. Event parsed: device, capability, new value
3. Event logged to event_log
4. Device snapshot updated in state cache
5. Event rules evaluated (is anyone listening? any automations?)
6. If notification needed: channel adapter sends message to relevant user(s)
7. If agent action needed: synthetic message through agent pipeline
```

---

## Storage Layout

```text
data/
├── db/
│   ├── users.db          # Users, households, channel mappings, task state
│   ├── memory.db         # Profiles, episodic facts, conversation history
│   ├── cache.db          # State cache: device snapshots, event log, run log, pending_action
│   └── scheduler.db      # Reminders, scheduled jobs
└── chroma/               # Vector embeddings for semantic memory search
```

All databases are SQLite (WAL mode) with schemas managed by Alembic. Chroma runs embedded (in-process). No external database services required.

---

## State Cache Tables

### `device_snapshot`

Last known state of every Homey device capability.

```text
device_snapshot
├── device_id         (from Homey)
├── capability        e.g. "onoff", "dim", "measure_temperature"
├── value             JSON-encoded current value
├── updated_at        timestamp of last known update
└── source            "homey_event" | "agent_action" | "poll" | "verify"
```

Refreshed: on home events, after agent write actions, and by a periodic background poll (configurable interval, default 5 min).

### `event_log`

Immutable append-only log of all significant events.

```text
event_log
├── id
├── event_type        "telegram_message" | "home_event" | "reminder_fired" | "agent_trigger"
├── household_id
├── user_id           (nullable for system events)
├── payload           JSON — full event details
└── created_at
```

Retention: configurable, default 90 days.

### `agent_run_log`

Record of each agent execution: what triggered it, what model was used, what tools were called, and what was returned.

```text
agent_run_log
├── id
├── household_id
├── user_id
├── trigger_event_id  (FK to event_log)
├── model_used
├── input_summary     truncated input for debugging
├── tools_called      JSON array of {tool, args, result, verified}
├── output_summary    truncated final response
├── duration_ms
├── tokens_used       {input, output}
└── created_at
```

Used for: competing action detection (check recent runs for this device), cost tracking, debugging.

---

## Competing Action Detection

Before the agent executes a write action on a device, it checks `agent_run_log` for any run in the last 60 seconds that also wrote to the same device. If found:

- If same user: proceed silently (user is following up their own request)
- If different user: agent informs the requester ("Emma just changed that — want me to override?")

The time window and behaviour are configurable via `COMPETING_ACTION_WINDOW_SECONDS`.

---

## Task State

A `task` represents a multi-step operation the agent is executing across multiple conversation turns. Single-turn requests do not create tasks. Tasks are created when the agent identifies that a goal requires multiple steps or spans multiple messages.

```text
task (in users.db)
├── id
├── household_id
├── user_id
├── title               short description, e.g. "Plan weekend dinner"
├── status              ACTIVE | AWAITING_INPUT | AWAITING_CONFIRMATION | COMPLETED | FAILED | CANCELLED
├── steps               JSON array: [{description, status, completed_at}, ...]
├── current_step        index into steps array
├── context             JSON — task-specific state (e.g. restaurant options found)
├── trigger_event_id    FK to event_log — what started this task
├── created_at
├── updated_at
└── completed_at
```

**Context injection:** When a user sends a message, the agent checks for any `ACTIVE` or `AWAITING_INPUT` tasks belonging to them. If found, task state is injected into the system prompt so the agent can resume the task coherently.

**User commands:**

- "What are you working on?" → agent lists active tasks
- "Cancel that" / "Forget about the dinner plan" → agent marks task `CANCELLED`
- "Continue" / resuming the conversation → agent picks up from `current_step`

**Task lifecycle:** Tasks are created by the agent (via a `create_task` tool call). They are updated after each step. Completed and cancelled tasks are retained for history but not injected into future context.

---

## Rate Limiting

Per-user rate limiting is applied as FastAPI middleware before any request reaches the agent. Uses `slowapi` (wraps the `limits` library).

Default: **50 requests per user per minute**, configurable via `RATE_LIMIT_PER_USER_PER_MINUTE`.

Rate limit key: Telegram user ID (not IP address — all Telegram traffic comes from Telegram's servers, so IP-based limiting would block everyone).

If exceeded: HTTP 429 returned to Telegram (Telegram will not retry message delivery for 429s). Agent sends the user a polite message: *"You're sending messages quickly — please wait a moment."*

Rate limiting is disabled in `development` and `test` environments.

---

## Health Endpoint

`GET /health` — returns component status as JSON. Used by Docker healthcheck.

See [observability.md](observability.md) for the full response schema and Docker Compose configuration.

---

## Graceful Degradation

Each component has a defined failure contract. The agent always informs the user when something is unavailable and continues with unaffected functionality.

See [graceful-degradation.md](graceful-degradation.md) for the full degradation matrix and per-component contracts.

---

## Security Considerations

- Telegram webhooks validated via `secret_token`
- All API keys in environment variables, never in code or git
- User authorisation: only registered household members can interact
- Homey token scoped to read/write only what is needed
- Policy Gate prevents high-impact actions without explicit confirmation
- No data sent to third parties except LLM API calls and Homey cloud MCP
- Conversation content is sent to Anthropic/OpenAI as part of API calls — consider this when discussing sensitive topics

---

## Deployment

Single Docker container. Multi-platform image (ARM64 + AMD64).

See [README.md](../README.md) for build and run instructions.
See [tech-stack.md](tech-stack.md) for full dependency list.
