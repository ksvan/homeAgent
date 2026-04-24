# Agent Flow

This page shows the end-to-end runtime path for HomeAgent: from the moment a
chat or scheduled trigger arrives, through context assembly and tool use, to the
response, persistence, and any future continuation work.

Preview:

![Agent Flow](diagrams/agent-flow.svg)

---

## What This Diagram Shows

### 1. Ingress and safety checks

The flow begins when HomeAgent receives:

- a normal chat message
- a confirmation callback
- a scheduled prompt fire
- a task-resume trigger

Before the agent runs, the runtime still applies the usual guardrails:

- source validation
- allowlist / feature checks
- user resolution
- rate limiting / per-user serialization
- slash-command interception

### 2. Context building

For normal agent runs, the runtime assembles context from:

- prompt files and time context
- user profile
- household profile
- household world model
- active task context
- available skills index
- relevant episodic memories
- recent conversation turns and summary

This is what makes the single-agent design work.

### 3. The model/tool loop

The agent does not just "answer once". It can loop through:

- reasoning over the built context
- deciding whether it needs tools
- calling MCP or internal tools
- receiving results back into the same run

This may repeat multiple times before the final answer is produced.

### 4. Policy gate and side effects

High-impact Homey actions are screened by the policy gate.

- safe/read-style work proceeds immediately
- risky actions can pause behind a pending confirmation
- the later callback path executes the confirmed action outside the original run

### 5. Tasks, reminders, and scheduled prompts

Internal tools can create or update:

- multi-step tasks
- reminders
- scheduled prompts
- event rules
- skill lookups
- future task resumes

Those future triggers come back into the system later and re-enter the runtime
through the same top-level flow.

### 6. Persistence and background work

After the run:

- the full turn is saved
- run logs and snapshots are updated
- admin/observability events are emitted
- memory extraction and summarization run in the background

---

## Mermaid Source

```mermaid
flowchart TB
    START["Chat message, callback,<br/>scheduled prompt, event,<br/>or task follow-up resume"] --> ENTRY["Webhook handler, dispatcher,<br/>or scheduler job"]
    ENTRY --> AUTH{Valid source,<br/>enabled feature, allowed user?}
    AUTH -->|No| DROP["Reject, drop, or mark failed"]
    AUTH -->|Yes| USER["Resolve user + household"]
    USER --> RATE{Rate limit /<br/>per-user lock OK?}
    RATE -->|No| THROTTLE["Return short error or wait"]
    RATE -->|Yes| CMD{Slash command?}
    CMD -->|Yes| CMDH["Deterministic command handler<br/>(no LLM run)"]
    CMD -->|No| TASKRES["Resolve active task /<br/>message attachment"]

    subgraph CONTEXT["Build working context"]
        PROMPTS["Prompt files + time_context"]
        DYNAMIC["User profile + household profile<br/>world model + active task<br/>pursuit state + skills index<br/>memories + recent turns"]
        CTX["assemble_context() output"]
        PROMPTS --> CTX
        DYNAMIC --> CTX
    end

    TASKRES --> CTX
    CTX --> AGENT["run_conversation()<br/>PydanticAI agent"]
    AGENT --> THINK["Model reasons over context,<br/>plans, asks, and chooses tools"]
    THINK --> NEED{Tool call needed?}
    NEED -->|No| OUT["Final assistant response"]
    NEED -->|Yes| KIND{Which kind of tool?}

    KIND --> HOMEY["Homey MCP"]
    KIND --> PROM["Prometheus MCP"]
    KIND --> TOOLS["Tools MCP"]
    KIND --> INTERNAL["Internal tools<br/>memory, calendar, world model,<br/>tasks, reminders, scheduled prompts,<br/>event rules, skills"]

    HOMEY --> POLICY{High-impact action<br/>requires confirmation?}
    POLICY -->|No| TOOLRES["Tool result returned to model"]
    POLICY -->|Yes| PENDING["Save PendingAction<br/>and send Yes/No prompt"]
    PENDING --> WAITCONF["Current run ends waiting for confirmation"]
    WAITCONF --> SAVE

    PROM --> TOOLRES
    TOOLS --> TOOLRES

    INTERNAL --> PURSUIT["Autonomous task pursuit tools<br/>record attempt, advance step,<br/>replan, follow-up, fail"]
    PURSUIT --> STATEWRITE["May create/update:<br/>task state + pursuit context,<br/>attempt checkpoints, reminders,<br/>scheduled prompts, event rules,<br/>world model, memory"]
    INTERNAL --> STATEWRITE
    STATEWRITE --> TOOLRES
    STATEWRITE --> SCHED["APScheduler / future triggers"]
    SCHED --> FUTURE["Reminder fire, scheduled prompt,<br/>or task follow-up resume later"]
    FUTURE --> ENTRY

    TOOLRES --> THINK

    CALLBACK["Later user callback"] --> EXECCONF["Execute confirmed action<br/>and notify user"]
    EXECCONF --> OBS
    EXECCONF --> REPLY

    OUT --> SAVE["Persist turn + run log + snapshots"]
    SAVE --> REPLY["Reply to chat"]
    SAVE --> BG["Background work:<br/>memory extraction + summarization"]
    SAVE --> OBS["Emit run / task / world / job events<br/>including attempts, follow-ups,<br/>resumes, replans, failures"]

    NOTE1["Same core agent loop is reused for<br/>normal chat, scheduled prompts,<br/>events, and task resumes"]:::note
    CTX -.-> NOTE1
    NOTE2["Pursuit state lets the agent remember<br/>attempts, retry budgets, next actions,<br/>and resume intent"]:::note
    STATEWRITE -.-> NOTE2

    classDef note fill:#f7f4e8,stroke:#8a6d3b,color:#5a4630;
```
