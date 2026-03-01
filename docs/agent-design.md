# Agent Design

This document defines how the agent behaves, its persona, how it is instructed, and how context is assembled for each conversation turn. Any agent working on this codebase should treat this as the source of truth for agent behaviour.

---

## Persona

The agent is a household AI assistant. It is:

- **Warm and natural** — not robotic, not overly formal. Like a capable and trusted family helper.
- **Concise by default** — gives short answers unless the user asks for detail.
- **Proactive within limits** — surfaces relevant information when useful, but does not add unsolicited commentary.
- **Household-aware** — knows the family, the home, and acts accordingly.
- **Honest about limitations** — does not guess at home device states; queries Homey rather than making things up.

The agent does not have a fixed name by default — this is configurable per household. Placeholder: **"Home"**.

---

## System Prompt Structure

The system prompt is assembled fresh for each conversation turn. It is composed of the following sections, in order:

### 1. Base Persona

```text
You are {agent_name}, the AI assistant for the {household_name} household.
You help with smart home control, personal tasks, reminders, and general questions.
You know the family well and remember past conversations.
Today is {current_date}. The time is {current_time} ({timezone}).
```

### 2. User Context

Who is currently speaking:

```text
You are speaking with {user_name}.
{user_profile_summary}
```

Example user profile summary:
> Kristian is the household admin. Prefers concise answers. Usually checks in during commute.

### 3. Household Context

Shared family knowledge:

```text
Household members:
{list of members with brief descriptors}

Shared context:
{household_profile_summary}
```

### 4. Active Task State (injected when tasks exist)

If the user has any `ACTIVE` or `AWAITING_INPUT` tasks, they are summarised and injected:

```text
You are currently working on the following tasks for {user_name}:
- "Plan weekend dinner" (step 2/4: restaurant options gathered, awaiting selection)
```

This allows the agent to pick up multi-step tasks coherently across conversation turns.

### 5. Home Context (injected for home-related queries)

Not always included — only when the query appears home-related, or on demand:

```text
Current home state (from Homey, as of {timestamp}):
{relevant device states}
```

### 6. Relevant Memories

Retrieved by semantic search against the current message:

```text
Relevant things to remember:
- {memory_1}
- {memory_2}
...
```

### 7. Conversation History

Recent messages from this conversation (last 20 turns or ~4000 tokens, whichever is less). Older conversation is compressed into a rolling summary prepended before the recent window:

```text
Earlier in this conversation:
{summary}

Recent messages:
[user]: ...
[assistant]: ...
```

---

## Prompt Files

The base persona, instructions, and home context are defined in editable markdown files
under the `prompts/` directory (path configurable via `PROMPTS_DIR` in `.env`).

```text
prompts/
├── persona.md        — Who the agent is and how it communicates (always included)
├── instructions.md   — Specific behavioural rules (always included)
└── home_context.md   — Home layout and device context (included for home-related queries)
```

Files are loaded at startup. An admin can hot-reload them without restarting the service
by sending `/reload` in Telegram.

### Template variables

Files support `{variable}` slots that are filled in at runtime before the prompt is sent
to the LLM. Available variables:

| Variable | Source | Example |
| --- | --- | --- |
| `{agent_name}` | `AGENT_NAME` in `.env` | `Home` |
| `{household_name}` | Household profile (DB) | `The Ås family` |
| `{current_date}` | System clock | `Sunday, 1 March 2026` |
| `{current_time}` | System clock | `08:32` |
| `{timezone}` | Household profile (DB) | `Europe/Oslo` |
| `{timestamp}` | State cache refresh time | `2026-03-01T08:30:00Z` |
| `{device_states}` | Homey state cache | _(formatted device list)_ |

Variables are only available in the files where they are listed in the file's
header comment. Unrecognised variables are passed through unchanged and logged
as a warning.

### Editing tips

- Comments wrapped in `<!-- ... -->` are stripped before the prompt is sent.
- The files are plain markdown — headings and bullet points are fine. The LLM
  reads them as plain text.
- Keep files focused. `persona.md` defines tone; `instructions.md` defines rules.
  Do not put rules in `persona.md` or vice versa.
- Changes take effect immediately after a `/reload`. No restart needed.

---

## Tools Available to the Agent

The agent has access to the following tools. Tools are registered via Pydantic AI's tool system.

| Tool | Description | Source |
| --- | --- | --- |
| `homey_*` | Control and query Homey devices | Homey MCP server |
| `set_reminder` | Create a reminder for any household member | Internal |
| `search_web` | Search the web for current information | Web search API |
| `get_weather` | Get current or forecast weather | Weather API |
| `get_time` | Get current time in a timezone | Internal |
| `update_user_profile` | Store a new fact about a user | Internal memory |
| `update_home_profile` | Store a new fact about the home | Internal memory |

Tools prefixed `homey_` are dynamically registered from the Homey MCP server's capability list at startup.

---

## Memory Extraction

After each completed conversation turn, a background task runs a lightweight LLM call to extract any new facts worth remembering. The extraction prompt looks for:

- New facts about a family member (preferences, habits, schedule)
- New facts about the home (new devices, changed routines, room assignments)
- Commitments made (agent promised to do something)
- Corrections to existing memories (user corrected something the agent said)

Extracted facts are stored in the episodic memory store and indexed for semantic retrieval.

**Extraction is conservative** — it only stores clearly stated facts, not inferences. If unsure, it does not store.

---

## Conversation Isolation vs. Shared Context

Each user has their own conversation thread. The agent does not share conversation history between users. However, the **household profile and episodic memories are shared** — if one family member tells the agent something about the household, others benefit from that context.

This means:

- Personal conversations stay private
- Household knowledge is collective

---

## Cross-User Reminders

When a user asks the agent to remind another household member:

1. Agent calls `set_reminder` tool with `target_user` field set to the other member
2. Stored in scheduler with target user ID, message, and time
3. At trigger time, message is sent directly to the target user via their registered channel
4. The original requester is not notified unless they ask

The reminder message is delivered as-is, or the agent can be asked to rephrase it.

---

## Guardrails and Limitations

- The agent **does not execute irreversible home actions** (e.g., unlocking doors, disabling alarms) without explicit confirmation.
- The agent **does not share one user's personal conversation** with another user.
- The agent **does not make up device states** — it queries Homey for current state.
- The agent **surfaces uncertainty** — if it does not know something, it says so rather than guessing.

---

## LLM Routing

The agent uses a `LLMRouter` class to select the appropriate model per task type. PydanticAI handles the mechanics of calling each provider; the router decides which model to use.

### Task Types and Default Models

| Task type | Default model | Rationale |
| --- | --- | --- |
| `CONVERSATION` | Claude Sonnet 4.5 | Primary — reasoning, tool use, natural language |
| `HOME_CONTROL` | Claude Sonnet 4.5 | Tool use quality matters for device actions |
| `PLANNING` | Claude Sonnet 4.5 | Multi-step reasoning |
| `MEMORY_EXTRACTION` | Claude Haiku 4.5 | Background task, lightweight, cheap |
| `SUMMARIZATION` | Claude Haiku 4.5 | Background task, no tool use needed |
| `EMBEDDING` | OpenAI text-embedding-3-small | Best-in-class for the price |
| `FALLBACK` | GPT-4o | Used if Anthropic API is unavailable |

### Fallback Behaviour

If the primary model call fails (API error, timeout, rate limit):

1. Log the failure to `agent_run_log`
2. Retry once with the same model after 2 seconds
3. If still failing: switch to fallback model and retry
4. If fallback also fails: return a graceful error message to the user

### Feature Flags

Model routing respects the `FeatureFlags` settings object (loaded from `.env`). Flags relevant to LLM routing:

| Flag | Default | Effect |
| --- | --- | --- |
| `FEATURE_CHEAP_BACKGROUND_MODELS` | `true` | Use Haiku/Mini for background tasks; disable to use Sonnet for everything |
| `FEATURE_FALLBACK_MODEL` | `true` | Enable automatic fallback to secondary provider |
| `FEATURE_LOCAL_MODEL` | `false` | Route background tasks to a local Ollama model (future) |

All model names and feature flags are configured via `.env`. See `.env.example` for full reference.

---

## Extending the Agent

To add a new capability:

1. Define a new tool function with `@agent.tool` decorator in `app/agent/tools/`
2. Register it in `app/agent/agent.py`
3. Update this document with the new tool entry in the tools table
4. If the tool is a new integration, create a guide in `docs/integrations/`
