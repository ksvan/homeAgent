# Agent Design

Status: implemented, active runtime reference
Last code check: 2026-06-26
Runtime entry points: `app/agent/agent.py`, `app/agent/context.py`,
`app/agent/runner.py`

This document describes the current HomeAgent runtime behavior: how prompts are assembled, what durable context is injected, and what tools the conversation agent can use.

---

## Persona

The agent is a household AI assistant. It is:

- **Warm and natural**: not robotic, not overly formal
- **Concise by default**: gives short answers unless the user asks for more
- **Proactive within limits**: surfaces relevant information when useful, but should not ramble
- **Household-aware**: knows the people, rooms, devices, and routines of the household
- **Honest about uncertainty**: queries live systems rather than pretending to know current state

The agent name is configurable through `AGENT_NAME`.

---

## System Prompt Structure

Split into a static part and a dynamic part in
[`app/agent/agent.py`](/Users/kristian/Documents/code/homeAgent/app/agent/agent.py)
(`_make_conversation_agent()`), so persona/instructions/tool schemas can form
a stable, cacheable prefix on providers that support prompt caching — see
`docs/prompt-caching-design.md`.

### 1. Static: `Agent(instructions=...)`

`prompts/persona.md` + `prompts/instructions.md`, loaded once via
`load_static_prompt_body()` and passed to the `Agent` constructor. No
per-call template variables — this content is byte-identical across every
call for every user/household. Not persisted into `message_history` (that's
what pydantic-ai's `instructions` mechanism, as opposed to `system_prompt`,
is for).

### 2. Dynamic: `@a.system_prompt`

Rebuilt fresh every run, ordered most-stable → most-volatile:

1. **Identity block** — `prompts/identity.md`, rendered via
   `render_identity_block(agent_name, household_name, user_name)`. The one
   part of "persona" that legitimately varies per call.
2. **Time context block**:

   ```text
   <time_context>
   {
     "current_time": "2026-03-28T15:32:00+01:00",
     "timezone": "Europe/Oslo"
   }
   </time_context>
   ```

3. `## User Profile`
4. `## Household Profile`
5. Current user block
6. `## Household Model` (structured world model from `users.db`, not free-text memory)
7. Skills index
8. Active task block
9. `## Conversation Summary`
10. `## Relevant Memories`

Each section is appended only when present.

### 3. Recent Conversation Turns

Recent conversation turns are **not** appended into the system prompt text. They are passed separately as `message_history` into the PydanticAI run so the model still sees the actual recent turn sequence, including tool calls.

---

## Context Assembly

`assemble_context()` in [`app/agent/context.py`](/Users/kristian/Documents/code/homeAgent/app/agent/context.py) currently loads:

1. user profile
2. household profile
3. formatted world model snapshot
4. recent conversation turns
5. optional rolling conversation summary
6. relevant episodic memories

This is the authoritative current behavior. Older design notes about injecting task state or `home_context.md` directly into every run are not the current runtime path.

---

## Prompt Files

The editable prompt files live in `prompts/`:

```text
prompts/
├── persona.md
├── instructions.md
├── identity.md
└── home_context.md
```

Current runtime behavior:

- `persona.md` — static, loaded into `Agent(instructions=...)`. No template
  variables (tone/style/behavior content only).
- `instructions.md` — static, loaded into `Agent(instructions=...)`. Already
  variable-free (JSON examples in the file use `{{...}}` escaping, unescaped
  once via `format_map({})` on load).
- `identity.md` — dynamic, rendered fresh per call via
  `render_identity_block()` into the `@a.system_prompt` suffix. Holds the
  one part of "persona" that legitimately varies per call.
- `home_context.md` exists, but is not currently injected by the conversation agent

Prompt files are cached in-process and reloaded when the admin issues `/reload`.

### Template variables

Files support runtime replacement via `str.format_map()`, with unknown placeholders left untouched. Only `identity.md` currently uses this — `persona.md`/`instructions.md` are loaded static (no variables to substitute):

| Variable | Source | Used in |
| --- | --- | --- |
| `{agent_name}` | `AGENT_NAME` setting | `identity.md` |
| `{household_name}` | household record | `identity.md` |
| `{user_name}` | current speaker | `identity.md` |

---

## Tools Available to the Agent

The conversation agent is built once and receives MCP toolsets plus built-in Python tools.

### MCP toolsets

When connected, the agent gets tools from:

- Homey MCP
- Prometheus MCP
- Tools MCP

### Built-in tools

Built-in tool families currently registered from `app/agent/tools/`:

| Tool | Purpose |
| --- | --- |
| `set_reminder` | Schedule a reminder message for a household member |
| `list_reminders` / `cancel_reminder` | Inspect and cancel reminders |
| `schedule_homey_action` | Schedule a future Homey action |
| `list_scheduled_actions` / `cancel_scheduled_action` | Inspect and cancel scheduled actions |
| `store_memory` | Store soft episodic memory |
| `forget_memory` | Delete episodic memories |
| `update_user_profile` | Persist stable user facts |
| `update_household_profile` | Persist stable household facts |
| calendar tools | Add/query imported calendars |
| `schedule_prompt` | Schedule a one-off or recurring autonomous prompt |
| `list_scheduled_prompts` / `cancel_scheduled_prompt` | Inspect and cancel scheduled prompts |
| `update_world_model` | Store structured household facts, aliases, routines, activities, goals |
| `remove_world_model_entry` | Remove structured world-model entries |
| `list_world_entities` | Inspect the household world model |

Important behavioral split:

- use **profile tools** for stable always-present facts
- use **world-model tools** for structured household entities and relationships
- use **episodic memory** for softer or more situational facts

---

## World Model in Agent Context

The world model is now part of every conversation run.

It provides canonical grounding for:

- household members
- member interests, goals, and activities
- places / rooms / zones
- devices and their locations
- linked calendars
- routines
- household facts

The formatter keeps this compact enough to include in the prompt on every run. If the model needs more detail than the compact section provides, it can call `list_world_entities`.

---

## Memory Extraction

After a successful conversation run, background tasks persist:

- a text-only message pair for summarization
- the full turn message list for model history reuse
- extracted episodic memories
- a conversation summary when thresholds are exceeded

Memory extraction remains conservative:

- it stores clearly stated durable facts
- it should not store transient device states or temporary outages
- structured household facts should prefer `update_world_model` over `store_memory`

---

## Conversation Isolation vs Shared Context

Current scoping rules:

- conversation history is per user
- user profiles are per user
- personal episodic memories are per user
- household profiles are shared across the household
- the household world model is shared across the household
- household-scoped episodic memories are shared across the household

The agent should never surface one user's personal conversation history or personal memories to another user.

---

## Scheduled and Autonomous Behavior

The agent can now create autonomous future work in two main ways:

- reminders
- scheduled prompts

Scheduled prompts are distinct from reminders:

- a reminder sends saved text later
- a scheduled prompt runs the agent later and delivers the fresh generated answer

This is the current low-risk mechanism for proactive behavior.

Detailed evolution proposal: [proactive-scheduled-behaviour-design.md](proactive-scheduled-behaviour-design.md)

---

## Guardrails and Limitations

- The agent does not assume live Homey state; it queries tools for that.
- High-impact actions can require explicit confirmation through the Policy Gate.
- Scheduled unattended actions are blocked if policy evaluation says they require confirmation.
- The agent can update the world model, but writes are still conservative and fully inspectable through admin endpoints.
- `home_context.md` is not currently a live runtime context source.

---

## LLM Routing

`LLMRouter` selects models by task type.

Current task classes:

| Task type | Typical model slot |
| --- | --- |
| `CONVERSATION` | primary model |
| `HOME_CONTROL` | primary model |
| `PLANNING` | primary model |
| `MEMORY_EXTRACTION` | background model when enabled |
| `SUMMARIZATION` | background model when enabled |
| `EMBEDDING` | embedding model |

Current routing notes:

- background extraction and summarization prefer the cheaper background model slot
- the primary slot is used otherwise
- the fallback slot is used when enabled and needed
- each slot can now bind to its own API key/provider

See [`app/agent/llm_router.py`](/Users/kristian/Documents/code/homeAgent/app/agent/llm_router.py).

---

## Extending the Agent

To add a capability:

1. add or update a tool module in [`app/agent/tools`](/Users/kristian/Documents/code/homeAgent/app/agent/tools)
2. register it in [`app/agent/agent.py`](/Users/kristian/Documents/code/homeAgent/app/agent/agent.py)
3. update this document if the new capability changes runtime behavior or the durable context model
