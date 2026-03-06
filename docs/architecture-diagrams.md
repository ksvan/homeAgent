# Architecture Diagrams

This document provides two architecture drawings based on the current HomeAgent codebase:

- High-level system architecture
- Detailed software architecture (runtime components and data flow)

---

## High-Level Architecture

```mermaid
flowchart TB
    U[Family Users] --> TG[Telegram]
    TG --> API[FastAPI Server\n/webhook/telegram, /health]

    subgraph HA[HomeAgent Runtime]
        API --> BOT[Message Dispatcher\napp/bot.py]
        BOT --> AGENT[Agent Orchestrator\nPydanticAI]
        AGENT --> ROUTER[LLM Router]
        ROUTER --> CLAUDE[Anthropic Claude]
        ROUTER --> OPENAI[OpenAI GPT / Embeddings]

        AGENT --> TOOLS[Tool Layer]
        TOOLS --> HOMEY[Homey MCP]
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

        BOT --> CACHE[State Cache Services]
        CACHE --> DBC[(cache.db)]

        BOT --> USERSVC[User/Profile Services]
        USERSVC --> DBU[(users.db)]

        SCHED[APScheduler] --> JOBS[Reminder + Cleanup Jobs]
        JOBS --> DBU
        JOBS --> DBC
        JOBS --> TG
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

    %% Context assembly
    CTX --> PROF[Profiles\napp/memory/profiles.py]
    CTX --> CONV[Conversation History\napp/memory/conversation.py]
    CTX --> EPI[Episodic Memory Search\napp/memory/episodic.py]
    CTX --> SNAP[Device Snapshots\napp/homey/state_cache.py]

    PROF --> DBU[(users.db)]
    CONV --> DBM[(memory.db)]
    EPI --> DBM
    EPI --> VEC[(sqlite-vec\nepisodic_memory_vec)]
    SNAP --> DBC[(cache.db)]

    %% Agent run
    CTX --> RUN[run_conversation\napp/agent/agent.py]
    RUN --> PR[Prompt Loader\napp/agent/prompts.py]
    RUN --> LLMR[LLM Router\napp/agent/llm_router.py]
    LLMR --> LLM[Anthropic/OpenAI Models]

    %% Tool + policy path — Homey (read/write, policy gate)
    RUN --> MCP[Homey MCP Tool Calls\napp/homey/mcp_client.py]
    MCP --> PG[Policy Evaluation\napp/policy/gate.py]
    PG --> DEC{Requires\nConfirmation?}

    DEC -->|No| CALL[Execute Tool]
    CALL --> VER[Post-write Verify\napp/homey/verify.py]
    VER --> DBC

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
```

