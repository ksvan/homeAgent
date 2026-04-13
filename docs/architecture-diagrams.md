# Architecture Diagrams

This document provides text diagrams for the current HomeAgent codebase. The Mermaid diagrams below are kept in sync with the runtime more often than the exported SVGs.

SVG exports currently in the repo:

- `docs/diagrams/architecture-high-level.svg`
- `docs/diagrams/architecture-detailed.svg`
- `docs/diagrams/main-path-startup-and-one-message.svg`
- `docs/diagrams/dev-vs-prod-from-start-sh.svg`
- `docs/diagrams/agent-flow.svg`

Preview:

![High-Level Architecture](diagrams/architecture-high-level.svg)
![Detailed Architecture](diagrams/architecture-detailed.svg)
![Main Path: Startup and One Message](diagrams/main-path-startup-and-one-message.svg)
![start.sh Mode Matrix](diagrams/dev-vs-prod-from-start-sh.svg)
![Agent Flow](diagrams/agent-flow.svg)

---

## High-Level Architecture

```mermaid
flowchart TB
    U[Telegram Users] --> API[FastAPI API<br>/webhook/telegram /health /admin]
    API --> BOT[Message Dispatcher<br>app/bot.py]

    subgraph RUNTIME[HomeAgent Runtime]
        BOT --> CMD[Slash Command Dispatcher]
        CMD --> CMDS[Command Handlers<br>/help /contextstats /history /schedule /prompts /status /users]

        BOT --> CTX[Context Assembly<br>profiles + world model + active task + summary + memories + skills index + recent turns]
        CTX --> AGENT[Conversation Agent<br>PydanticAI]
        AGENT --> ROUTER[LLM Router]
        ROUTER --> CLAUDE[Anthropic models]
        ROUTER --> OPENAI[OpenAI models]

        AGENT --> TOOLS[Tool Layer]
        TOOLS --> HOMEY[Homey MCP]
        TOOLS --> PROM[Prometheus MCP]
        TOOLS --> TOOLSMCP[Tools MCP]
        TOOLS --> BUILTIN[Built-in tools<br>memory / reminders / actions / tasks / scheduled prompts / world model / event rules / skills]

        HOMEY --> POLICY[Policy Gate]
        POLICY --> PENDING[Pending Confirmation Flow]
        PENDING --> U

        CTX --> DBU[(users.db)]
        CTX --> DBM[(memory.db)]
        AGENT --> DBC[(cache.db)]
        DBM --> VEC[(sqlite-vec)]

        STARTUP[Startup sync] --> WMBOOT[bootstrap_world_model]
        WMBOOT --> DBU
        STARTUP --> HPROF[refresh_home_profile]
        HPROF --> DBM
        STARTUP --> SKILLS[SkillRegistry<br>app/skills]
        SKILLS --> CTX

        AGENT --> BG[Background tasks<br>memory extract + conversation summarize]
        BG --> DBM

        SCHED[APScheduler] --> JOBS[restore reminders/actions/prompts<br>cleanup jobs]
        JOBS --> DBU
        JOBS --> DBC
        JOBS --> DBM

        ADMIN[Admin API / Dashboard] --> SSE[SSE Event Bus]
        ADMIN --> SKILLS
        ADMIN --> CTRLTAB[Control Loop tab<br>dispatcher + event rules + active tasks]
        CTRLTAB --> EVENTBUS[Inbound Event Bus]
        EVENTBUS --> DISPATCH[Event Dispatcher]
        DISPATCH --> AGENT
        AGENT --> SSE
        SCHED --> SSE
        DISPATCH --> SSE
    end
```

---

## Detailed Software Architecture

```mermaid
flowchart LR
    TGU[Telegram Update] --> WH[/POST /webhook/telegram<br>app/api/webhooks.py/]
    WH --> SEC{Secret header valid?}
    SEC -->|No| REJ[403 reject]
    SEC -->|Yes| CH[TelegramChannel<br>process_update]
    CH --> BOT[handle_incoming_message<br>app/bot.py]

    BOT --> ACL{Allowlisted +<br>not rate-limited?}
    ACL -->|No| DROP[Drop / throttle response]
    ACL -->|Yes| USER[Get/Create User<br>users.db]

    USER --> CMDCHK{Starts with / ?}
    CMDCHK -->|Yes| DISPATCH[commands.dispatcher<br>try_dispatch]
    DISPATCH --> CMDH[commands.handlers]
    CMDH --> CMDRESP[Command response]

    CMDCHK -->|No or unhandled| CTX[assemble_context]
    CTX --> PROF[Profiles<br>memory.db]
    CTX --> WORLD[World model snapshot<br>users.db]
    CTX --> TASKCTX[Active task context<br>users.db]
    CTX --> HIST[Recent turns + summary<br>memory.db]
    CTX --> MEM[Episodic retrieval<br>sqlite-vec]
    CTX --> SKIDX[Available skills index<br>SkillRegistry]

    CTX --> RUN[run_conversation<br>app/agent/agent.py]
    RUN --> PROMPTS[persona.md + instructions.md<br>+ time_context block]
    RUN --> MODEL[LLM Router]
    RUN --> MCPHOMEY[Homey MCP]
    RUN --> MCPPROM[Prometheus MCP]
    RUN --> MCPTOOLS[Tools MCP]
    RUN --> BUILTIN[Built-in tools<br>memory/reminders/actions/tasks/prompts/world-model/event-rules/skills]
    SKTOOL[get_skill / list_skills<br>app/agent/tools/skills.py] --> SKIDX
    RUN --> SKTOOL

    MCPHOMEY --> POL[Policy evaluate]
    POL --> NEED{Confirmation needed?}
    NEED -->|No| EXEC[Execute tool]
    NEED -->|Yes| SAVEPA[Save PendingAction<br>cache.db]
    SAVEPA --> INLINE[Inline Yes/No prompt]
    INLINE --> CH

    CH --> CALLBACK[Callback query]
    CALLBACK --> CONFIRM[Execute or cancel pending action]
    CONFIRM --> MCPHOMEY

    EXEC --> VERIFY[verify_after_write]
    VERIFY --> SNAP[(device snapshots)]

    RUN --> RESP[Agent output]
    RESP --> SAVEPAIR[save_message_pair]
    RESP --> SAVETURN[save_conversation_turn]
    RESP --> RUNLOG[write agent run log]
    RESP --> EVT[emit run/cmd/world/task/job events]
    RESP --> BG[async memory extraction + summarization]

    BG --> DBM[(memory.db)]
    EVT --> DBC[(cache.db)]

    START[FastAPI lifespan startup] --> MCPSTART[Start Homey / Prom / Tools MCP]
    MCPSTART --> RELOAD[reload_agent]
    START --> SCH[Start scheduler]
    SCH --> RESTORE[restore reminders/actions/prompts]
    START --> BOOT[bootstrap_world_model + refresh_home_profile]
    START --> EDISP[Start inbound event dispatcher]
    EDISP --> EB[Inbound event bus]
    EB --> MATCH[match enabled EventRules]
    MATCH --> CTRL[resolve/reuse control Task]
    CTRL --> RUNNER[run_agent_turn]
    RUNNER --> RUN

    ADMIN[/admin APIs<br>/stats /stream /scheduler /world-model /event-rules /tasks /skills/] --> EVT
    ADMIN --> EB
```

---

## Startup and One Message Path

```mermaid
flowchart TB
    START[start.sh up] --> DC[docker compose build + up -d]
    DC --> APP[homeagent container<br>python -m app]
    APP --> MIG[Alembic upgrade heads]
    MIG --> LIFE[FastAPI lifespan]

    LIFE --> MCP[Start Homey / Prom / Tools MCP]
    LIFE --> RELOAD[reload agent with connected toolsets<br>reset prompt + skill caches]
    LIFE --> SCH[Start scheduler + restore reminders/actions/prompts + cleanup]
    LIFE --> DISP[Start inbound event dispatcher]
    LIFE --> BOOT[fire-and-forget startup syncs]
    BOOT --> WM[bootstrap_world_model]
    BOOT --> HP[refresh_home_profile]
    LIFE --> TG[Initialize Telegram channel]

    TG --> WH[Incoming webhook update]
    WH --> MSG[handle_incoming_message]
    MSG --> CMD{slash command?}
    CMD -->|Yes handled| CR[Return command response]
    CMD -->|No| CTX[assemble_context<br>profiles + world model + skills index + memories]
    CTX --> RUN[run_conversation]
    RUN --> POL{tool call requires confirm?}
    POL -->|Yes| PEND[pending action + inline buttons]
    POL -->|No| OUT[agent output]
    PEND --> CB[user callback]
    CB --> OUT

    OUT --> SAVE[persist turn + run log + snapshots]
    SAVE --> BG[async extract + summarize]
    SAVE --> REPLY[Telegram reply]

    DISP --> EVBUS[inbound event bus]
    EVBUS --> RULES[event rules]
    RULES --> CTRL[control task]
    CTRL --> RUN
```

---

## `start.sh` Mode Matrix

```mermaid
flowchart TB
    SH[start.sh] --> MODE{Mode arg}

    MODE -->|up default| UP["docker compose build + up -d"]
    MODE -->|logs| LOGS["docker compose logs -f"]
    MODE -->|stop| STOP["docker compose down"]
    MODE -->|restart| RESTART["docker compose down -> build -> up -d"]

    UP --> STACK[Running stack<br>homeagent + tools + prometheus-mcp + cloudflared]
    RESTART --> STACK

    STACK --> RUNTIME[Webhook runtime]

    LOGS --> OBS[Observe only]
    STOP --> DOWN[Stack stopped]
```
