# Memory Design

HomeAgent now uses a layered memory model that combines always-present profiles, a structured household world model, semantic episodic recall, and conversation continuity.

The important architectural change since the earlier design is that **world structure is no longer only implied by prose**. Durable household entities now live in a first-class world model in `users.db`.

---

## Memory Layers

### Layer 1: Profiles

Profiles remain the smallest always-present structured memory layer.

They live in `memory.db` and are updated through:

- `update_user_profile`
- `update_household_profile`

Examples:

**User Profile**

```json
{
  "name": "Kristian",
  "preferred_language": "Norwegian",
  "communication_style": "concise, technical"
}
```

**Household Profile**

```json
{
  "location": "Oslo, Norway",
  "timezone": "Europe/Oslo"
}
```

Profiles are injected into every run when present.

### Layer 2: Household World Model

The world model is the canonical structured household layer. It lives in `users.db`.

Current entity families:

- `HouseholdMember`
- `MemberInterest`
- `MemberGoal`
- `MemberActivity`
- `Place`
- `DeviceEntity`
- `CalendarEntity`
- `RoutineEntity`
- `Relationship`
- `WorldFact`

Current bootstrap sources:

- users
- imported calendars
- Homey zones and devices
- seed facts

This layer is used for:

- alias resolution
- room/device disambiguation
- member/calendar linking
- routine grounding
- structured household facts

At runtime it is formatted into a compact `## Household Model` section and injected into every conversation run.

### Layer 3: Episodic Memory

Episodic memory stores softer, relevance-based facts that do not need to be present in every run.

It lives in `memory.db`, with embeddings stored in the `episodic_memory_vec` sqlite-vec virtual table.

Schema:

```text
EpisodicMemory
├── id
├── household_id
├── user_id           nullable; null means household-visible
├── content
├── embedding_id      rowid in sqlite-vec, nullable if embedding failed
├── source_run_id
├── created_at
├── importance        critical | important | normal | ephemeral
└── last_used_at
```

Use episodic memory for:

- personal preferences
- communication style observations
- situational but durable facts
- soft patterns that do not map cleanly to a world-model entity

Do **not** use episodic memory for:

- live device state
- current time/date
- transient failures
- strongly structured household facts that should live in the world model

### Layer 4: Conversation History

Conversation continuity is stored in `memory.db` in two forms:

- `ConversationTurn`: full PydanticAI message history for each turn
- `ConversationMessage`: text-only user/assistant pairs used for summarization

Supporting summary table:

```text
ConversationSummary
├── id
├── user_id
├── summary
├── covers_through_message_id
└── created_at
```

Current runtime behavior:

- the recent context window is the last **10** turns
- only the newest **3** turns keep full tool-return payloads
- older turns keep the tool-call structure but replace `ToolReturnPart` content with `[result omitted]`
- rolling summarization begins once text history exceeds **20** messages
- each summarization pass compresses the oldest **10** messages into a cumulative summary

### Layer 5: Working Memory

Working memory is the per-request assembled context, not a stored table.

Current assembly order:

1. persona prompt file
2. instructions prompt file
3. user profile
4. household profile
5. household world model
6. conversation summary
7. relevant episodic memories

Recent conversation turns are passed separately as `message_history`.

---

## Why the World Model Changed the Design

Before the world-model work, HomeAgent had to infer household structure from:

- prompt prose
- profiles
- semantic memory hits

That was enough for recall, but weak for durable reasoning about:

- who a calendar belongs to
- what room a device is in
- what alias maps to which canonical entity
- what routines mean operationally

The world model fixes that by making those relationships queryable and inspectable instead of leaving them as loose text.

---

## Storage Split

### `users.db`

Contains:

- users / households
- calendars
- tasks
- scheduled prompts
- action policies
- world-model entities

### `memory.db`

Contains:

- profiles
- episodic memories
- conversation messages
- conversation turns
- conversation summaries
- sqlite-vec index

### `cache.db`

Contains runtime operational state, not durable household knowledge:

- device snapshots
- event log
- agent run log
- pending confirmations

---

## Update Flow

```text
User sends message
        │
        ▼
Agent responds
        │
        ├── save text pair for summarization
        ├── save full turn for future model history
        ├── write run log and snapshots
        │
        ▼
Background tasks
        │
        ├── episodic memory extraction
        └── conversation summarization when thresholds are exceeded
```

World-model writes are different:

- explicit user statements can be written immediately through `update_world_model`
- admin can edit the model through `/admin/world-model*` endpoints
- startup bootstrap populates the baseline model from trusted sources

There is not yet a fully enabled broad background proposal pipeline that auto-promotes extracted memories into world-model changes.

---

## Retrieval Behavior

### Profiles

Always injected when present.

### World model

Always injected when non-empty.

### Episodic memories

Retrieved by semantic similarity against the current user message:

1. embed the query
2. search sqlite-vec
3. scope results to:
   - household memories
   - personal memories for the current user
4. update `last_used_at` on retrieved results

If embeddings are unavailable, retrieval falls back to recency-based memory selection.

### Conversation history

Recent turns are loaded directly from `ConversationTurn` so the model sees prior tool calls and does not re-run already completed actions unnecessarily.

---

## Tools and Intended Use

| Tool | Use it for |
| --- | --- |
| `update_user_profile` | stable personal facts that should always be in context |
| `update_household_profile` | stable shared household facts that should always be in context |
| `update_world_model` | structured household entities, aliases, routines, goals, activities, facts |
| `remove_world_model_entry` | delete structured world-model entries |
| `store_memory` | soft episodic facts and preferences |
| `forget_memory` | remove episodic memories |

Rule of thumb:

- **profile** for small always-needed facts
- **world model** for canonical household structure
- **episodic memory** for nuance and softer recall

---

## Scoping Rules

| Memory type | Scope |
| --- | --- |
| user profile | per user |
| household profile | shared household |
| world model | shared household |
| episodic personal memory | per user |
| episodic household memory | shared household |
| conversation history | per user |

The agent must not expose one user's personal conversation history or personal episodic memories to another user.

---

## Lifecycle

### Episodic memory lifecycle

Episodic memories are pruned by importance tier:

| Tier | Intended retention |
| --- | --- |
| `critical` | never expire automatically |
| `important` | long-lived |
| `normal` | medium-lived |
| `ephemeral` | short-lived |

Freshness is measured by `last_used_at`, falling back to `created_at`.

Near-duplicate suppression is done with vector similarity before insert.

### World model lifecycle

The world model is intentionally more conservative:

- baseline is bootstrapped from trusted sources
- explicit user/admin corrections are allowed
- writes are inspectable
- future proposal/reconciliation remains a later phase

---

## Current Gaps / Future Work

- contradiction detection between new facts and existing memory
- proposal pipeline for promoting repeated stable facts into structured world-model updates
- richer relationship use at runtime beyond the current compact formatter
- stronger task-state memory for multi-step conversational work
