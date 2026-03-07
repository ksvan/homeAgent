# Memory Design

HomeAgent uses a layered memory system designed to make the agent feel genuinely familiar with the household over time, without relying on fine-tuning or unlimited context windows.

---

## Memory Layers

### Layer 1 — Structural Memory (profiles)

Stored as JSON in SQLite. Updated by the agent via explicit tool calls (`update_user_profile`, `update_household_profile`).

**User Profile** (one per household member):

```json
{
  "name": "Kristian",
  "preferred_name": "Kristian",
  "wake_time": "07:00",
  "communication_style": "concise, technical",
  "preferred_language": "Norwegian"
}
```

**Household Profile** (one per household):

```json
{
  "location": "Oslo, Norway",
  "timezone": "Europe/Oslo",
  "guest_bedroom_default_temp": "18°C"
}
```

Profiles are always injected into every system prompt — they represent facts the agent should always have available. Unlike episodic memories, profiles are not retrieved by relevance: they are always present.

The agent writes profile updates when the user shares stable personal facts ("my name is Kristian", "I prefer Norwegian"). The user can also ask the agent to update or view the profile directly.

---

### Layer 2 — Episodic Memory (extracted facts)

Stable facts extracted from conversations by a background LLM call (Haiku) after each exchange. Stored in SQLite and indexed in sqlite-vec for semantic retrieval.

**Schema:**

```text
EpisodicMemory
├── id                UUID primary key
├── household_id      scoping key
├── user_id           nullable — null means household-level fact
├── content           "Sarah prefers the bedroom lights at 20% when watching TV"
├── embedding_id      ID of the corresponding vector in sqlite-vec
├── source_run_id     which agent run produced this memory
└── created_at
```

**How memories get in:**

- **Auto-extraction** (default): after every agent run, a background Haiku call analyses the exchange and extracts any stable facts worth remembering. This runs fire-and-forget — it never blocks the response.
- **Explicit store**: the agent calls `store_memory(content, scope)` when the user directly asks it to remember something.

**What is extracted (and what is not):**

The extraction prompt is strict. It only extracts facts that are durable across sessions: user preferences, household facts, named entities, recurring patterns. It explicitly rejects current device states, dates, weather, temporary situations, and conversational filler.

**Retrieval:**

1. Embed current user message (OpenAI text-embedding-3-small)
2. Query sqlite-vec for top-K similar memories (scoped to this user + household-level)
3. Inject top results into the system prompt as a `## Relevant Memories` section

---

### Layer 3 — Conversation History

Full message history stored in SQLite per user. Used for:

- Recent context: last 20 message pairs injected verbatim as `message_history`
- Long-term continuity: older conversations are compressed into a rolling summary

**Schema:**

```text
ConversationMessage
├── id            UUID primary key
├── user_id       FK to users.db (cross-DB reference, no SQL FK)
├── role          "user" | "assistant"
├── content
└── created_at

ConversationSummary
├── id            UUID primary key
├── user_id       unique — one summary per user
├── summary       LLM-generated bullet-point summary
├── covers_through_message_id   FK to the last message this summary covers
└── created_at
```

**Compaction strategy:**
After each agent run, a background task checks the user's message count. When it exceeds 50 messages, the oldest 30 are summarised (incorporating any existing summary as context prefix) by Haiku into 4–8 bullet points. The summary is upserted (replacing the previous one), and the 30 old messages are deleted.

This runs fire-and-forget — it never blocks the response.

The assembled conversation history for a prompt:

```text
[If a summary exists]:
## Conversation Summary
<bullet points>

[Recent messages, verbatim, passed as message_history]:
User: ...
Assistant: ...
```

---

### Layer 4 — Working Memory (assembled per request)

Not stored — built fresh on each request. This is the assembled system prompt passed to the LLM.

**Assembly order:**

1. Base persona (from `prompts/persona.md` — date, time, timezone filled in)
2. Instructions (from `prompts/instructions.md`)
3. Home context (from `prompts/home_context.md` — static layout, naming, routines)
4. User profile (from Layer 1)
5. Household profile (from Layer 1)
6. Conversation summary (from Layer 3, if any)
7. Relevant episodic memories (from Layer 2, top-K by similarity)

Conversation message history is passed separately as `message_history` (not inside the system prompt).

**Device states are not pre-loaded.** The agent queries Homey live via MCP when it needs current device state. This keeps the system prompt lean and avoids stale data.

---

## Memory Update Flow

```text
User sends message
        │
        ▼
Agent responds
        │
        ▼
Response returned to user immediately
        │
Background tasks triggered (fire-and-forget, never block the response)
        │
  ┌─────┴─────────────────────┐
  │                           │
  ▼                           ▼
Save message pair to    Auto-extract memories (Haiku call)
conversation history          │
                         Any stable facts?
                              │
                    ┌─────────┴──────────┐
                    ▼                    ▼
              Store in episodic    Emit mem.extract
              DB + sqlite-vec      event to admin feed
              index

  ▼
Check message count — if > 50:
  Summarize oldest 30 → upsert ConversationSummary
  Delete the 30 old messages
  Emit mem.summarize event to admin feed
```

---

## Agent Memory Tools

The agent has four memory tools available:

| Tool | When to use |
| ---- | ----------- |
| `store_memory(content, scope)` | User explicitly asks to remember something |
| `update_user_profile(key, value)` | Stable personal fact (name, preference, routine) |
| `update_household_profile(key, value)` | Stable household fact (location, device nicknames) |
| `forget_memory(content_substring)` | User asks to forget something, or fact is wrong |

---

## Memory Scoping

| Memory type | Scope | Who can see it |
| ----------- | ----- | -------------- |
| User profile | Per-user | That user + agent |
| Household profile | Household | All household members via agent |
| Episodic — personal | Per-user | That user only |
| Episodic — household | Household | All members via agent |
| Conversation history | Per-user | That user only |

The agent **never surfaces one user's personal memories or conversation to another user.** Household-level memories are fair game for all members.

---

## Memory Correction

Users can correct memories:

- "That's wrong, remember that I actually prefer..." → agent calls `store_memory` or `update_user_profile`
- "Forget that" → agent calls `forget_memory(content_substring)` to delete matching entries
- Auto-extracted facts can be overridden the same way

---

## Future Considerations

- **Memory aging**: Rarely-recalled memories decay in relevance score over time
- **Contradiction detection**: New memories checked against existing ones for conflicts
- **Vector DB migration**: The `store_memory` / `search_memories` interface is the clean boundary — sqlite-vec can be swapped for a full vector DB without touching callers
- **Graph memory**: A separate memory type for relationship graphs (person ↔ device ↔ room) is planned as a future layer alongside episodic
