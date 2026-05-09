# Architecture Diagrams

This document provides text diagrams for the current HomeAgent codebase. The Mermaid diagrams below are the source of truth for the exported SVGs.

SVG exports currently in the repo:

- `docs/diagrams/architecture-high-level.svg`
- `docs/diagrams/architecture-detailed.svg`
- `docs/diagrams/architecture-layered-components.svg`
- `docs/diagrams/main-path-startup-and-one-message.svg`
- `docs/diagrams/dev-vs-prod-from-start-sh.svg`
- `docs/diagrams/agent-flow.svg`
- `docs/diagrams/autonomous-task-pursuit.svg`
- `docs/diagrams/email-intake-flow.svg`
- `docs/diagrams/mac-mini-deployment.svg`

Preview:

![High-Level Architecture](diagrams/architecture-high-level.svg)
![Detailed Architecture](diagrams/architecture-detailed.svg)
![Layered Components Architecture](diagrams/architecture-layered-components.svg)
![Main Path: Startup and One Message](diagrams/main-path-startup-and-one-message.svg)
![start.sh / prod.sh Mode Matrix](diagrams/dev-vs-prod-from-start-sh.svg)
![Agent Flow](diagrams/agent-flow.svg)
![Email Intake Flow](diagrams/email-intake-flow.svg)
![Mac Mini Deployment](diagrams/mac-mini-deployment.svg)

---

## High-Level Architecture

```mermaid
flowchart TB
    U[Telegram Users] --> API[FastAPI API<br>/webhook/telegram /webhook/agentmail<br>/webhook/homey/event /webhook/flights/* /health /admin]
    ES[Email Senders] --> AM[AgentMail Inbox]
    AM --> API
    HF[Homey Advanced Flows] --> API
    FP[Flight Providers] --> API

    API --> BOT[Telegram Message Dispatcher<br>app/bot.py]
    API --> EMAILIN[Email Intake<br>Svix verify + dedupe + queue]
    API --> EVENTIN[Homey Event Intake]
    API --> FLIGHTIN[Flight Webhook Intake]

    subgraph RUNTIME[HomeAgent Runtime]
        BOT --> CMD[Slash Command Dispatcher]
        CMD --> CMDS[Command Handlers<br>/help /contextstats /history /schedule /prompts /status /users /me]

        BOT --> CTX[Context Assembly<br>profiles + world model + active task + pursuit state + summary + memories + skills index + recent turns]
        CTX --> AGENT[Conversation Agent<br>PydanticAI]
        AGENT --> ROUTER[LLM Router]
        ROUTER --> CLAUDE[Anthropic models]
        ROUTER --> OPENAI[OpenAI models]

        AGENT --> TOOLS[Tool Layer]
        TOOLS --> HOMEY[Homey MCP]
        TOOLS --> PROM[Prometheus MCP]
        TOOLS --> TOOLSMCP[Tools MCP]
        TOOLS --> AGMAILAPI[AgentMail API]
        TOOLS --> BUILTIN[Built-in tools<br>memory / reminders / actions / tasks / task pursuit / scheduled prompts / world model / event rules / skills / email / flights]
        BUILTIN --> PURSUIT[Autonomous Task Pursuit<br>attempts + step advancement + replans + follow-ups + failure logic]

        HOMEY --> POLICY[Policy Gate]
        POLICY --> PENDING[Pending Confirmation Flow]
        PENDING --> U

        EMAILIN --> EMAILQ[Email Queue<br>EmailMessage + EmailIntakeConfirmation]
        EMAILQ --> EMAILPROC[Email Processor<br>fetch + preprocess + extract signals]
        EMAILPROC --> EMAILPROMPT[Telegram email confirmation]
        EMAILPROMPT --> U
        U --> EMAILCB[Telegram email callback]
        EMAILCB --> RUNNER[run_agent_turn<br>trigger=email_confirmed]
        RUNNER --> AGENT

        EVENTIN --> EVENTBUS[Inbound Event Bus]
        EVENTBUS --> DISPATCH[Event Dispatcher]
        DISPATCH --> AGENT

        FLIGHTIN --> FLSVC[Flight Monitor Service]
        FLSVC --> AGENT

        CTX --> DBU[(users.db)]
        CTX --> DBM[(memory.db)]
        AGENT --> DBC[(cache.db)]
        EMAILQ --> DBC
        FLSVC --> DBU
        DBM --> VEC[(sqlite-vec)]

        STARTUP[Startup sync] --> WMBOOT[bootstrap_world_model]
        WMBOOT --> DBU
        STARTUP --> HPROF[refresh_home_profile]
        HPROF --> DBM
        STARTUP --> SKILLS[SkillRegistry<br>app/skills]
        SKILLS --> CTX

        AGENT --> BG[Background tasks<br>memory extract + conversation summarize]
        BG --> DBM

        SCHED[APScheduler] --> JOBS[restore reminders/actions/prompts<br>task follow-ups + email retry/stale-lock + cleanup jobs]
        JOBS --> DBU
        JOBS --> DBC
        JOBS --> DBM
        JOBS --> EMAILQ

        ADMIN[Admin API / Dashboard] --> SSE[SSE Event Bus]
        ADMIN --> SKILLS
        ADMIN --> CTRLTAB[Control Loop tab]
        ADMIN --> EMAILTAB[Email tab]
        CTRLTAB --> EVENTBUS
        EMAILTAB --> DBC
        AGENT --> SSE
        SCHED --> SSE
        DISPATCH --> SSE
        EMAILPROC --> SSE
        FLSVC --> SSE
    end
```

---

## Layered Components Architecture

This SVG is hand-authored rather than Mermaid-generated so the drawing can keep a conventional architecture layout: external interfaces at the top, application/runtime/core layers in the middle, persistence at the bottom, and a support/control plane on the side spanning the stack.

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

    USER --> CMDCHK{Starts with slash?}
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
    RUN --> BUILTIN[Built-in tools<br>memory/reminders/actions/tasks/task-pursuit/prompts/world-model/event-rules/skills/email/flights]
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

    AMEV[AgentMail Svix Event] --> AMWH[/POST /webhook/agentmail/]
    AMWH --> AMSEC{Feature + size +<br>Svix/inbox valid?}
    AMSEC -->|No| AMREJ[Reject or ignore]
    AMSEC -->|Yes| AMDEDUP[Deduplicate delivery + message ids]
    AMDEDUP --> AMROW[Persist EmailMessage<br>cache.db]
    AMROW --> AMPROC[process_email_message]
    AMPROC --> AMFETCH[Fetch full message<br>AgentMail API]
    AMFETCH --> AMMAP[Resolve sender via<br>ChannelMapping email]
    AMMAP --> AMPRE[Preprocess + extract signals]
    AMPRE --> AMPROMPT[Telegram email intake prompt]
    AMPROMPT --> CH
    CH --> AMCB[Callback email_confirm/email_cancel]
    AMCB --> RUNNER[run_agent_turn<br>trigger=email_confirmed]
    RUNNER --> RUN

    RUN --> RESP[Agent output]
    RESP --> SAVEPAIR[save_message_pair]
    RESP --> SAVETURN[save_conversation_turn]
    RESP --> RUNLOG[write agent run log]
    RESP --> EVT[emit run/cmd/world/task/job/email/flight events]
    RESP --> BG[async memory extraction + summarization]

    BG --> DBM[(memory.db)]
    EVT --> DBC[(cache.db)]

    START[FastAPI lifespan startup] --> MCPSTART[Start Homey / Prom / Tools MCP]
    MCPSTART --> RELOAD[reload_agent]
    START --> SCH[Start scheduler]
    SCH --> RESTORE[restore reminders/actions/prompts/task follow-ups<br>register email worker jobs]
    START --> BOOT[bootstrap_world_model + refresh_home_profile]
    START --> EDISP[Start inbound event dispatcher]
    EDISP --> EB[Inbound event bus]
    EB --> MATCH[match enabled EventRules]
    MATCH --> CTRL[resolve/reuse control Task]
    CTRL --> RUNNER
    PURSUIT --> TASKCTX
    PURSUIT --> EVT
    PURSUIT --> SCH

    ADMIN[/admin APIs<br>/stats /stream /scheduler /world-model /event-rules /tasks /skills /email/] --> EVT
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
    LIFE --> EMAILJOBS[Register email retry + stale-lock + retention jobs<br>when FEATURE_EMAIL_CHANNEL=true]
    LIFE --> DISP[Start inbound event dispatcher]
    LIFE --> BOOT[fire-and-forget startup syncs]
    BOOT --> WM[bootstrap_world_model]
    BOOT --> HP[refresh_home_profile]
    LIFE --> TG[Initialize Telegram channel]

    TG --> WH[Incoming Telegram webhook]
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

    AM[AgentMail webhook] --> AMQ[persist EmailMessage + dedupe]
    AMQ --> AMP[fetch + preprocess + extract]
    AMP --> AMCONF[Telegram email confirmation]
    AMCONF --> RUN

    DISP --> EVBUS[inbound event bus]
    EVBUS --> RULES[event rules]
    RULES --> CTRL[control task]
    CTRL --> RUN
    RUN --> PURSUIT[task pursuit tools<br>attempts + follow-ups + fail/complete]
    PURSUIT --> SAVE
    PURSUIT --> SCH
```

---

## `start.sh` / `prod.sh` Mode Matrix

```mermaid
flowchart TB
    LOCAL[start.sh] --> MODE{Mode arg}
    PROD[scripts/prod.sh] --> PMODE{Command}

    MODE -->|up default| UP["docker compose build + up -d"]
    MODE -->|logs| LOGS["docker compose logs -f"]
    MODE -->|stop| STOP["docker compose down"]
    MODE -->|restart| RESTART["docker compose down -> build -> up -d"]

    PMODE -->|bootstrap| BOOT["ssh remote<br>mkdir + verify Docker Compose"]
    PMODE -->|migrate| MIGRATE["local backup + stop<br>rsync code + .env + data<br>remote build + up"]
    PMODE -->|deploy| DEPLOY["rsync code only<br>remote build + up"]
    PMODE -->|status/logs/backup| OPS["remote inspect / logs / backup"]

    UP --> STACK[Running stack<br>homeagent + tools + prometheus-mcp + cloudflared]
    RESTART --> STACK
    MIGRATE --> STACK
    DEPLOY --> STACK

    STACK --> RUNTIME[Webhook runtime]
    LOGS --> OBS[Observe only]
    STOP --> DOWN[Stack stopped]
    BOOT --> READY[Remote target ready]
    OPS --> OBS
```

---

## Email Intake Flow

```mermaid
sequenceDiagram
    participant Sender as Email sender
    participant AgentMail
    participant API as /webhook/agentmail
    participant Cache as cache.db
    participant Worker as Email processor
    participant Telegram
    participant Agent as Agent runner

    Sender->>AgentMail: Send or forward email
    AgentMail->>API: Svix-signed message.received
    API->>API: Validate feature, size, inbox, signature
    API->>Cache: Deduplicate and persist EmailMessage
    API-->>AgentMail: ok
    API->>Worker: Start background processing
    Worker->>AgentMail: Fetch full message
    Worker->>Worker: Preprocess, extract signals, bound source text
    Worker->>Cache: Store NEEDS_CONFIRMATION + token
    Worker->>Telegram: Ask mapped user to process email?
    Telegram->>API: email_confirm callback
    API->>Agent: run_agent_turn(trigger=email_confirmed)
    Agent->>Cache: Run logs, events, final email status
    Agent-->>Telegram: Result
```

---

## Mac Mini Deployment

```mermaid
flowchart LR
    DEV[Development Mac<br>working tree + current data] --> SCRIPT[scripts/prod.sh]
    SCRIPT --> SSH[SSH]
    SCRIPT --> RSYNC[rsync]

    subgraph MINI[Production Mac mini]
        PATH[~/homeAgent]
        ENV[.env]
        DATA[data/<br>SQLite + vector/runtime data + backups]
        COMPOSE[Docker Compose]
        STACK[homeagent + tools + prometheus-mcp + cloudflared]
    end

    SSH --> PATH
    RSYNC --> PATH
    PATH --> ENV
    PATH --> DATA
    PATH --> COMPOSE
    COMPOSE --> STACK

    MIGRATE[prod.sh migrate<br>backup local + stop local stack<br>sync code + .env + data] --> SCRIPT
    DEPLOY[prod.sh deploy<br>sync code only<br>preserve remote .env and data] --> SCRIPT
    STACK --> PUBLIC[Cloudflare tunnel + LAN webhooks]
    PUBLIC --> USERS[Telegram / AgentMail / Homey / flight providers]
```
