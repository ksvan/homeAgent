# Architecture Diagrams

This document provides text diagrams for the current HomeAgent codebase. The Mermaid diagrams below are kept in sync with the runtime more often than the exported SVGs.

SVG exports currently in the repo:

- `docs/diagrams/architecture-high-level.svg`
- `docs/diagrams/architecture-detailed.svg`
- `docs/diagrams/architecture-layered-components.svg`
- `docs/diagrams/main-path-startup-and-one-message.svg`
- `docs/diagrams/dev-vs-prod-from-start-sh.svg`
- `docs/diagrams/agent-flow.svg`
- `docs/diagrams/autonomous-task-pursuit.svg`

Preview:

![High-Level Architecture](diagrams/architecture-high-level.svg)
![Detailed Architecture](diagrams/architecture-detailed.svg)
![Layered Components Architecture](diagrams/architecture-layered-components.svg)
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

        BOT --> CTX[Context Assembly<br>profiles + world model + active task + pursuit state + summary + memories + skills index + recent turns]
        CTX --> AGENT[Conversation Agent<br>PydanticAI]
        AGENT --> ROUTER[LLM Router]
        ROUTER --> CLAUDE[Anthropic models]
        ROUTER --> OPENAI[OpenAI models]

        AGENT --> TOOLS[Tool Layer]
        TOOLS --> HOMEY[Homey MCP]
        TOOLS --> PROM[Prometheus MCP]
        TOOLS --> TOOLSMCP[Tools MCP]
        TOOLS --> BUILTIN[Built-in tools<br>memory / reminders / actions / tasks / task pursuit / scheduled prompts / world model / event rules / skills]
        BUILTIN --> PURSUIT[Autonomous Task Pursuit<br>attempts + step advancement + replans + follow-ups + failure logic]

        HOMEY --> POLICY[Policy Gate]
        POLICY --> PENDING[Pending Confirmation Flow]
        PENDING --> U

        PURSUIT --> DBU
        PURSUIT --> SCHED
        PURSUIT --> SSE

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

        SCHED[APScheduler] --> JOBS[restore reminders/actions/prompts<br>task follow-ups + cleanup jobs]
        JOBS --> DBU
        JOBS --> DBC
        JOBS --> DBM

        ADMIN[Admin API / Dashboard] --> SSE[SSE Event Bus]
        ADMIN --> SKILLS
        ADMIN --> CTRLTAB[Control Loop tab<br>dispatcher + event rules + active tasks<br>attempts + retries + follow-ups]
        CTRLTAB --> EVENTBUS[Inbound Event Bus]
        EVENTBUS --> DISPATCH[Event Dispatcher]
        DISPATCH --> AGENT
        AGENT --> SSE
        SCHED --> SSE
        DISPATCH --> SSE
    end
```

---

## Layered Components Architecture

```mermaid
flowchart TB
    subgraph EXT["External Interfaces"]
        TG["Telegram Bot API<br/>webhook updates + replies"]
        HOMEYEXT["Homey / Homey Pro<br/>devices, zones, flows"]
        PROMEXT["Prometheus<br/>metrics queries"]
        ADMINUSER["Admin Browser<br/>dashboard + SSE"]
        LLMEXT["LLM Providers<br/>Anthropic + OpenAI"]
    end

    subgraph EDGE["HTTP / Process Boundary"]
        API["FastAPI Main App<br/>/webhook/telegram /health"]
        ADMINAPI["FastAPI Admin App<br/>/admin/* /stream"]
        WEBHOOKS["Webhook Handlers<br/>Telegram + Homey events"]
        LIFESPAN["App Lifespan<br/>startup, shutdown, background tasks"]
    end

    subgraph CHANNELS["Channel + Delivery Layer"]
        CHREG["Channel Registry"]
        TGCHAN["TelegramChannel"]
        DELIVERY["Message Delivery<br/>chat replies + callbacks"]
    end

    subgraph CONTROL["Control Plane"]
        EVENTBUS["Inbound Event Bus"]
        DISPATCHER["Event Dispatcher"]
        RULES["Event Rules"]
        CTRL["Control Task Resolver"]
        SSE["Admin SSE Event Stream"]
        POLICY["Policy Gate<br/>confirmation decisions"]
        PENDING["Pending Action Flow"]
    end

    subgraph AGENTLAYER["Agent Runtime"]
        RUNNER["agent_run()<br/>unified entry point"]
        LOCKS["Per-user run locks"]
        CONTEXT["Context Assembly<br/>profiles, world, memories,<br/>active task, pursuit state"]
        AGENT["PydanticAI Conversation Agent"]
        ROUTER["LLM Router"]
        PROMPTS["Prompt + Skill Registry"]
    end

    subgraph TOOLS["Tool / Capability Layer"]
        INTERNAL["Built-in Agent Tools<br/>memory, calendar, reminders,<br/>actions, tasks, pursuit,<br/>scheduled prompts, world model,<br/>event rules, skills"]
        HOMEYMCP["Homey MCP Client"]
        PROMMCP["Prometheus MCP Client"]
        TOOLSMCP["Tools MCP Client"]
    end

    subgraph DOMAIN["Domain Services"]
        TASKS["Task Service<br/>steps, links, resumes"]
        PURSUIT["Autonomous Task Pursuit<br/>attempts, retry budget,<br/>follow-ups, failure logic"]
        WORLD["World Model Service"]
        MEMORY["Memory Service<br/>profiles, summaries, retrieval"]
        SCHEDPROMPTS["Scheduled Prompt Service"]
        REMINDERS["Reminder + Action Services"]
        VERIFY["Post-write Verification"]
    end

    subgraph SCHED["Scheduler / Background Work"]
        APS["APScheduler"]
        JOBS["Jobs<br/>reminders, actions,<br/>scheduled prompts, task resumes"]
        CLEANUP["Cleanup Jobs<br/>retention + stale tasks"]
        BG["Async Background Tasks<br/>memory extraction + summarization"]
    end

    subgraph DATA["Persistence"]
        USERSDB[("users.db<br/>users, households, tasks,<br/>task steps, links, rules,<br/>world model, scheduled prompts")]
        MEMORYDB[("memory.db<br/>profiles, turns, summaries,<br/>episodic memories")]
        CACHEDB[("cache.db<br/>pending actions, run logs,<br/>events, snapshots")]
        VEC[("sqlite-vec<br/>memory embeddings")]
    end

    TG --> API
    ADMINUSER --> ADMINAPI
    HOMEYEXT --> WEBHOOKS
    API --> WEBHOOKS
    ADMINAPI --> SSE
    ADMINAPI --> RULES
    ADMINAPI --> TASKS

    WEBHOOKS --> CHREG
    CHREG --> TGCHAN
    TGCHAN --> DELIVERY
    DELIVERY --> TG

    WEBHOOKS --> EVENTBUS
    EVENTBUS --> DISPATCHER
    DISPATCHER --> RULES
    RULES --> CTRL
    CTRL --> TASKS
    DISPATCHER --> RUNNER

    WEBHOOKS --> RUNNER
    JOBS --> RUNNER
    RUNNER --> LOCKS
    LOCKS --> CONTEXT
    CONTEXT --> AGENT
    AGENT --> ROUTER
    ROUTER --> LLMEXT
    PROMPTS --> CONTEXT
    PROMPTS --> AGENT

    AGENT --> INTERNAL
    AGENT --> HOMEYMCP
    AGENT --> PROMMCP
    AGENT --> TOOLSMCP
    HOMEYMCP --> HOMEYEXT
    PROMMCP --> PROMEXT

    INTERNAL --> TASKS
    INTERNAL --> PURSUIT
    INTERNAL --> WORLD
    INTERNAL --> MEMORY
    INTERNAL --> SCHEDPROMPTS
    INTERNAL --> REMINDERS
    HOMEYMCP --> POLICY
    POLICY --> PENDING
    PENDING --> DELIVERY
    HOMEYMCP --> VERIFY

    TASKS --> USERSDB
    PURSUIT --> TASKS
    PURSUIT --> APS
    WORLD --> USERSDB
    MEMORY --> MEMORYDB
    MEMORYDB --> VEC
    SCHEDPROMPTS --> USERSDB
    REMINDERS --> USERSDB
    VERIFY --> CACHEDB

    LIFESPAN --> APS
    LIFESPAN --> DISPATCHER
    LIFESPAN --> BG
    APS --> JOBS
    APS --> CLEANUP
    JOBS --> USERSDB
    CLEANUP --> USERSDB
    CLEANUP --> MEMORYDB
    CLEANUP --> CACHEDB
    BG --> MEMORY

    RUNNER --> CACHEDB
    RUNNER --> SSE
    TASKS --> SSE
    PURSUIT --> SSE
    DISPATCHER --> SSE
    JOBS --> SSE
    ADMINAPI --> USERSDB
    ADMINAPI --> MEMORYDB
    ADMINAPI --> CACHEDB
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
    CTX --> TASKCTX[Active task + pursuit context<br>users.db]
    CTX --> HIST[Recent turns + summary<br>memory.db]
    CTX --> MEM[Episodic retrieval<br>sqlite-vec]
    CTX --> SKIDX[Available skills index<br>SkillRegistry]

    CTX --> RUN[run_conversation<br>app/agent/agent.py]
    RUN --> PROMPTS[persona.md + instructions.md<br>+ time_context block]
    RUN --> MODEL[LLM Router]
    RUN --> MCPHOMEY[Homey MCP]
    RUN --> MCPPROM[Prometheus MCP]
    RUN --> MCPTOOLS[Tools MCP]
    RUN --> BUILTIN[Built-in tools<br>memory/reminders/actions/tasks/task-pursuit/prompts/world-model/event-rules/skills]
    BUILTIN --> PURSUIT[Task pursuit operations<br>record attempt / advance step / replan / follow-up / fail]
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
    RESP --> EVT[emit run/cmd/world/task/job events<br>attempts/follow-ups/resumes/replans/failures]
    RESP --> BG[async memory extraction + summarization]

    BG --> DBM[(memory.db)]
    EVT --> DBC[(cache.db)]

    START[FastAPI lifespan startup] --> MCPSTART[Start Homey / Prom / Tools MCP]
    MCPSTART --> RELOAD[reload_agent]
    START --> SCH[Start scheduler]
    SCH --> RESTORE[restore reminders/actions/prompts/task follow-ups]
    START --> BOOT[bootstrap_world_model + refresh_home_profile]
    START --> EDISP[Start inbound event dispatcher]
    EDISP --> EB[Inbound event bus]
    EB --> MATCH[match enabled EventRules]
    MATCH --> CTRL[resolve/reuse control Task]
    CTRL --> RUNNER[run_agent_turn]
    RUNNER --> RUN
    PURSUIT --> TASKCTX
    PURSUIT --> EVT
    PURSUIT --> SCH

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
    LIFE --> SCH[Start scheduler + restore reminders/actions/prompts/task follow-ups + cleanup]
    LIFE --> DISP[Start inbound event dispatcher]
    LIFE --> BOOT[fire-and-forget startup syncs]
    BOOT --> WM[bootstrap_world_model]
    BOOT --> HP[refresh_home_profile]
    LIFE --> TG[Initialize Telegram channel]

    TG --> WH[Incoming webhook update]
    WH --> MSG[handle_incoming_message]
    MSG --> CMD{slash command?}
    CMD -->|Yes handled| CR[Return command response]
    CMD -->|No| CTX[assemble_context<br>profiles + world model + active task + pursuit state + skills index + memories]
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
    RUN --> PURSUIT[task pursuit tools<br>attempts + follow-ups + fail/complete]
    PURSUIT --> SAVE
    PURSUIT --> SCH
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
