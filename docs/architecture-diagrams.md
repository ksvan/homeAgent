# Architecture Diagrams

This document provides two architecture drawings based on the current HomeAgent codebase:

- High-level system architecture
- Detailed software architecture (runtime components and data flow)

SVG exports (generated from Mermaid source below):

- `docs/diagrams/architecture-high-level.svg`
- `docs/diagrams/architecture-detailed.svg`
- `docs/diagrams/main-path-startup-and-one-message.svg`
- `docs/diagrams/dev-vs-prod-from-start-sh.svg`

Preview:

![High-Level Architecture](diagrams/architecture-high-level.svg)
![Detailed Architecture](diagrams/architecture-detailed.svg)
![Main Path: Startup and One Message](diagrams/main-path-startup-and-one-message.svg)
![Dev vs Prod from start.sh](diagrams/dev-vs-prod-from-start-sh.svg)

---

## High-Level Architecture

```mermaid
flowchart TB
    U[Family Users] --> TG[Telegram]
    TG --> API[FastAPI Server\n/webhook/telegram, /health, /admin]

    subgraph HA[HomeAgent Runtime]
        API --> BOT[Message Dispatcher\napp/bot.py]
        BOT --> AGENT[Agent Orchestrator\nPydanticAI]
        AGENT --> ROUTER[LLM Router]
        ROUTER --> CLAUDE[Anthropic Claude]
        ROUTER --> OPENAI[OpenAI GPT / Embeddings]

        AGENT --> TOOLS[Tool Layer]
        TOOLS --> HOMEY[Homey MCP\nsimple schema by default]
        TOOLS --> PROM[Prometheus MCP\nservices/prometheus-mcp]
        TOOLS --> REMINDERS[Reminder Tools]
        TOOLS --> SEARCH[Search/Scrape Tools]
        TOOLS --> EXEC[Python/Bash Tools]

        AGENT --> POLICY[Policy Gate]
        POLICY --> PENDING[Pending Confirmation Flow]
        PENDING --> TG

        AGENT --> MEMORY[Memory Services]
        MEMORY --> DBM[(memory.db)]
        MEMORY --> VEC[(sqlite-vec index)]

        BOT --> BG[Background Tasks]
        BG --> EXT[Auto Memory Extraction\nHaiku]
        BG --> SUM[Conversation Summarization\nHaiku]
        EXT --> DBM
        SUM --> DBM

        BOT --> CACHE[State Cache Services]
        CACHE --> DBC[(cache.db)]

        BOT --> USERSVC[User/Profile Services]
        USERSVC --> DBU[(users.db)]

        SCHED[APScheduler] --> JOBS[Reminder + Cleanup Jobs]
        JOBS --> DBU
        JOBS --> DBC
        JOBS --> TG

        ADMIN[Admin Dashboard\n/admin] --> CTRL[Control Plane\napp/control/]
        CTRL --> SSE[SSE Event Bus]
    end
```

---

## Detailed Software Architecture

```mermaid
flowchart LR
    %% Entry + channel
    TGU[Telegram Update] --> WH[/POST /webhook/telegram\napp/api/webhooks.py/]
    WH --> CH[TelegramChannel\napp/channels/telegram.py]
    CH --> BOT[handle_incoming_message\napp/bot.py]

    %% Core message handling
    BOT --> ACL{Allowlisted User?}
    ACL -->|No| DROP[Silent Drop]
    ACL -->|Yes| RL{Rate Limited?}
    RL -->|Yes| RLMSG[Rate Limit Message]
    RL -->|No| USER[Get/Create User\nusers.db]
    USER --> CTX[Context Assembly\napp/agent/context.py]

    %% Context assembly — memory only, no device states
    CTX --> PROF[Profiles\napp/memory/profiles.py]
    CTX --> CONV[Conversation History\napp/memory/conversation.py]
    CTX --> EPI[Episodic Memory Search\napp/memory/episodic.py]

    PROF --> DBU[(users.db)]
    CONV --> DBM[(memory.db)]
    EPI --> DBM
    EPI --> VEC[(sqlite-vec\nepisodic_memory_vec)]

    %% Agent run
    CTX --> RUN[run_conversation\napp/agent/agent.py]
    RUN --> PR[Prompt Loader\napp/agent/prompts.py]
    RUN --> LLMR[LLM Router\napp/agent/llm_router.py]
    LLMR --> LLM[Anthropic/OpenAI Models]

    %% Tool + policy path — Homey (simple schema, policy gate)
    RUN --> MCP[Homey MCP — Simple Schema\n7 everyday tools\napp/homey/mcp_client.py]
    MCP --> PG[Policy Evaluation\napp/policy/gate.py]
    PG --> DEC{Requires\nConfirmation?}

    DEC -->|No| CALL[Execute Tool]
    CALL --> VER[Post-write Verify\napp/homey/verify.py]
    VER --> DBC[(cache.db)]

    DEC -->|Yes| PEND[Save PendingAction\napp/policy/pending.py]
    PEND --> DBC
    PEND --> PROMPT[Send Inline Yes/No Prompt]
    PROMPT --> CH

    %% Callback confirmation loop
    CH --> CB[Callback Query Handler]
    CB --> PLOOK[Lookup PendingAction]
    PLOOK --> EXEC2[Execute or Cancel Pending Action]
    EXEC2 --> MCP

    %% Prometheus MCP — read-only, no policy gate
    RUN --> PMCP[Prometheus MCP Tool Calls\napp/prometheus/mcp_client.py]
    PMCP --> PROMSVC[services/prometheus-mcp\nFastMCP server]
    PROMSVC --> PROMAPI[Prometheus HTTP API\nLAN]

    %% Persist response + logs
    RUN --> RESP[Assistant Response]
    RESP --> SAVE[Save Message Pair]
    SAVE --> DBM
    RESP --> LOG[Run/Event Logging]
    LOG --> DBC

    %% Background memory tasks — fire-and-forget
    RESP --> BG[Background Tasks\nasyncio.ensure_future]
    BG --> EXTR[Auto Memory Extraction\napp/memory/extraction.py\nHaiku]
    BG --> SMRZ[Conversation Summarization\napp/memory/conversation.py\nHaiku]
    EXTR --> DBM
    SMRZ --> DBM

    %% Scheduler subsystem
    subgraph SCH[Scheduler Subsystem]
        SE[Scheduler Engine\napp/scheduler/engine.py]
        RM[Reminder Scheduling\napp/scheduler/reminders.py]
        CJ[Cleanup Jobs\napp/scheduler/cleanup.py]
        SJ[Job Executors\napp/scheduler/jobs.py]
        SE --> RM
        SE --> CJ
        RM --> SJ
    end

    SJ --> CH
    RM --> DBU
    CJ --> DBC

    %% Control plane
    subgraph CP[Control Plane]
        ADMIN[/admin dashboard/] --> EVT[SSE Event Bus\napp/control/events.py]
        EVT --> FEED[Live Feed]
    end

    LOG --> EVT
    SJ --> EVT
    EXTR --> EVT
    SMRZ --> EVT
```
