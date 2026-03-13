# Architecture Diagrams

This document provides architecture and flow drawings based on the current HomeAgent codebase.

SVG exports:

- `docs/diagrams/architecture-high-level.svg`
- `docs/diagrams/architecture-detailed.svg`
- `docs/diagrams/main-path-startup-and-one-message.svg`
- `docs/diagrams/dev-vs-prod-from-start-sh.svg` *(now documents current `start.sh` mode matrix)*

Preview:

![High-Level Architecture](diagrams/architecture-high-level.svg)
![Detailed Architecture](diagrams/architecture-detailed.svg)
![Main Path: Startup and One Message](diagrams/main-path-startup-and-one-message.svg)
![start.sh Mode Matrix](diagrams/dev-vs-prod-from-start-sh.svg)

---

## High-Level Architecture

```mermaid
flowchart TB
    U[Telegram Users] --> API[FastAPI Webhook API\n/webhook, /health]
    API --> BOT[Message Dispatcher\napp/bot.py]

    subgraph RUNTIME[HomeAgent Runtime]
        BOT --> CMD[Slash Command Dispatcher]
        CMD --> CMDS[Command Handlers\n/help /contextstats /history /schedule /status /users]

        BOT --> AGENT[Agent Orchestrator\nPydanticAI]
        AGENT --> ROUTER[LLM Router]
        ROUTER --> CLAUDE[Anthropic Claude]
        ROUTER --> OPENAI[OpenAI Models]

        AGENT --> TOOLS[Tool Layer]
        TOOLS --> HOMEY[Homey MCP]
        TOOLS --> PROM[Prometheus MCP]
        TOOLS --> TOOLSMCP[Tools MCP]
        TOOLS --> BUILTIN[Built-in Tools\nreminders/actions/memory]

        HOMEY --> POLICY[Policy Gate]
        POLICY --> PENDING[Pending Confirmation Flow]
        PENDING --> U

        AGENT --> DBU[(users.db)]
        AGENT --> DBM[(memory.db)]
        AGENT --> DBC[(cache.db)]
        DBM --> VEC[(sqlite-vec)]

        AGENT --> BG[Background Memory Tasks\nextract + summarize]
        BG --> DBM

        SCHED[APScheduler] --> JOBS[Restore + Cleanup Jobs]
        JOBS --> DBU
        JOBS --> DBM
        JOBS --> DBC

        ADMIN[Admin API / Dashboard] --> SSE[SSE Event Bus]
        AGENT --> SSE
        SCHED --> SSE
    end
```

---

## Detailed Software Architecture

```mermaid
flowchart LR
    TGU[Telegram Update] --> WH[/POST /webhook/telegram\napp/api/webhooks.py/]
    WH --> SEC{Secret header valid?}
    SEC -->|No| REJ[403 reject]
    SEC -->|Yes| CH[TelegramChannel\nprocess_update]
    CH --> BOT[handle_incoming_message\napp/bot.py]

    BOT --> ACL{Allowlisted +\nnot rate-limited?}
    ACL -->|No| DROP[Drop / throttle response]
    ACL -->|Yes| USER[Get/Create User\nusers.db]

    USER --> CMDCHK{Starts with / ?}
    CMDCHK -->|Yes| DISPATCH[commands.dispatcher\ntry_dispatch]
    DISPATCH --> CMDH[commands.handlers registry]
    CMDH --> CMDRESP[Command response]

    CMDCHK -->|No or unhandled| CTX[assemble_context\nprofiles/history/episodic]
    CTX --> RUN[run_conversation\napp/agent/agent.py]

    RUN --> PROMPTS[Persona + Instructions]
    RUN --> MODEL[LLM Router -> Claude/OpenAI]
    RUN --> MCPHOMEY[Homey MCP tool calls]
    RUN --> MCPPROM[Prometheus MCP]
    RUN --> MCPTOOLS[Tools MCP]

    MCPHOMEY --> POL[Policy evaluate]
    POL --> NEED{Confirmation needed?}
    NEED -->|No| EXEC[Execute + verify]
    NEED -->|Yes| SAVEPA[Save PendingAction\ncache.db]
    SAVEPA --> INLINE[Inline Yes/No prompt]
    INLINE --> CH

    CH --> CALLBACK[Callback query]
    CALLBACK --> CONFIRM[Execute or cancel pending action]
    CONFIRM --> MCPHOMEY

    RUN --> RESP[Agent output]
    RESP --> SAVEPAIR[save_message_pair\nmemory.db]
    RESP --> SNAP[update_snapshots_from_tool_calls\ncache.db]
    RESP --> EVT[emit run/cmd events\nSSE + cache.db]
    RESP --> BG[async extraction + summarization]
    BG --> DBM[(memory.db)]

    SCHED[Scheduler engine] --> RESTORE[restore reminders/actions]
    SCHED --> CLEAN[cleanup logs/memories/tasks]
    CLEAN --> DBU[(users.db)]
    CLEAN --> DBC[(cache.db)]
    CLEAN --> DBM

    ADMIN[/admin APIs\n/stats /stream /memory /scheduler/] --> EVT
```

---

## Startup and One Message Path

```mermaid
flowchart TB
    START[start.sh up] --> DC[docker compose build + up -d]
    DC --> APP[homeagent container\npython -m app]
    APP --> MIG[Alembic upgrade heads]
    MIG --> RUN[_run(): main webhook app + admin app]

    RUN --> LIFE[FastAPI lifespan startup]
    LIFE --> MCP[Start Homey/Prom/Tools MCP]
    LIFE --> SCH[Start scheduler + restore jobs + cleanup registration]
    LIFE --> TG[Initialize TelegramChannel]

    TG --> WH[Incoming webhook update]
    WH --> MSG[handle_incoming_message]
    MSG --> CMD{slash command?}
    CMD -->|Yes handled| CR[Return command response]
    CMD -->|No| AG[assemble_context + run_conversation]
    AG --> POL{Homey write needs confirm?}
    POL -->|Yes| PEND[pending action + inline buttons]
    POL -->|No| OUT[agent output]
    PEND --> CB[user callback]
    CB --> OUT

    OUT --> SAVE[persist messages + snapshots + run logs]
    SAVE --> BG[async memory extraction/summarization]
    SAVE --> REPLY[Telegram reply]
```

---

## `start.sh` Mode Matrix (Current)

```mermaid
flowchart TB
    SH[start.sh] --> MODE{Mode arg}

    MODE -->|up (default)| UP[docker compose build && up -d]
    MODE -->|logs| LOGS[docker compose logs -f]
    MODE -->|stop| STOP[docker compose down]
    MODE -->|restart| RESTART[docker compose down -> build -> up -d]

    UP --> STACK[Running stack: tools + homeagent + cloudflared]
    RESTART --> STACK

    STACK --> RUNTIME[Webhook runtime\n(homeagent APP_ENV=production)]

    LOGS --> OBS[Observe only\nno state change]
    STOP --> DOWN[Stack stopped]
```
