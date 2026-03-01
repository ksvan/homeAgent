# Memory Design

HomeAgent uses a layered memory system designed to make the agent feel genuinely familiar with the household over time, without relying on fine-tuning or unlimited context windows.

---

## Memory Layers

### Layer 1 — Structural Memory (explicit profiles)

Stored as JSON in SQLite. Manually curated by the agent over time, or directly by users.

**User Profile** (one per household member):
```json
{
  "name": "Kristian",
  "preferred_name": "Kristian",
  "role": "admin",
  "timezone": "Europe/Oslo",
  "language": "en",
  "preferences": {
    "response_style": "concise",
    "morning_briefing": true
  },
  "known_facts": [
    "Works from home on Fridays",
    "Allergic to cats",
    "Prefers the bedroom at 19°C at night"
  ]
}
```

**Household Profile** (one per household):
```json
{
  "name": "The Andersen Family",
  "timezone": "Europe/Oslo",
  "home_layout": {
    "floors": 2,
    "rooms": ["living room", "kitchen", "master bedroom", "kids room", "office"]
  },
  "routines": {
    "weekday_morning": "Kids leave at 7:45. Kristian works from home.",
    "bedtime": "Kids in bed at 21:00 on school nights."
  },
  "known_facts": [
    "Dog named Max, a Golden Retriever",
    "Weekly groceries delivered Thursday afternoon"
  ]
}
```

These profiles are the agent's "ground truth" about the family and home. They are injected into every system prompt.

---

### Layer 2 — Episodic Memory (extracted facts)

Facts extracted from conversations by a background LLM call after each exchange. Stored in SQLite with metadata, and indexed in Chroma for semantic retrieval.

**Schema:**
```
EpisodicMemory
├── id
├── household_id
├── user_id (nullable — null means household-level)
├── content          "Sarah prefers the bedroom lights at 20% when watching TV"
├── embedding        vector (1536-dim, OpenAI text-embedding-3-small)
├── source_type      "conversation" | "user_stated" | "agent_observed"
├── created_at
├── last_recalled_at
└── recall_count
```

Memories are retrieved by semantic similarity to the current user message, before each LLM call. Top-K most relevant memories are injected into the system prompt.

**Retrieval logic:**
1. Embed current user message
2. Query Chroma for top-10 similar memories (scoped to this user + household-level)
3. Filter by recency score (recently recalled memories ranked higher)
4. Inject top-5 into system prompt

---

### Layer 3 — Conversation History

Full message history stored in SQLite per user. Used for:
- Recent context (last 20 messages injected verbatim)
- Long-term summaries (older conversations compressed)

**Schema:**
```
Message
├── id
├── user_id
├── role             "user" | "assistant"
├── content
├── channel          "telegram" | "whatsapp" | ...
├── created_at
└── summary_id (nullable — links to a summary that replaces older messages)

ConversationSummary
├── id
├── user_id
├── content          LLM-generated summary of a message batch
├── covers_from      timestamp
├── covers_to        timestamp
└── created_at
```

**Compaction strategy:**
A scheduled job runs nightly. For each user, messages older than 7 days that haven't been summarised yet are batched (up to 50 at a time) and sent to a lightweight LLM for summarisation. The summary is stored, and the raw messages are retained (not deleted) but excluded from context assembly.

The assembled conversation history for a prompt looks like:

```
[If any summaries exist]:
Earlier conversations:
<summary 1>
<summary 2>

[Recent messages, verbatim]:
User: ...
Assistant: ...
```

---

### Layer 4 — Working Memory (assembled per request)

Not stored — built fresh on each request. This is the assembled system prompt passed to the LLM.

**Assembly order:**
1. Base persona (static)
2. User profile (from Layer 1)
3. Household profile (from Layer 1)
4. Home context (from Homey MCP, if home-related query)
5. Relevant episodic memories (from Layer 2, top-5)
6. Conversation summaries + recent messages (from Layer 3)

**Token budget management:**
- Profiles: ~400 tokens reserved
- Memories: ~500 tokens reserved (top-5 at ~100 tokens each)
- Summaries: ~600 tokens reserved
- Recent messages: ~4000 tokens reserved
- Tools/response: remainder

If total exceeds model context, oldest summaries and lowest-relevance memories are dropped first.

---

## Memory Update Flow

```
User sends message
        │
        ▼
Agent responds
        │
        ▼
Background task triggered (async, does not block response)
        │
  ┌─────┴─────┐
  │           │
  ▼           ▼
Save message  Extract memories (LLM call)
to history         │
              ┌────┴────┐
              ▼         ▼
         New facts?   Profile updates?
              │              │
              ▼              ▼
         Store in       Update user/
         Chroma +       household profile
         episodic DB    in SQLite
```

---

## Memory Scoping

| Memory type | Scope | Who can see it |
|---|---|---|
| User profile | Per-user | That user + agent |
| Household profile | Household | All household members via agent |
| Episodic — user | Per-user | That user only |
| Episodic — household | Household | All members via agent |
| Conversation history | Per-user | That user only |

The agent **never surfaces one user's personal memories or conversation to another user.** Household-level memories are fair game for all members.

---

## Memory Correction

Users can correct memories:
- "That's wrong, remember that I actually prefer..." → agent updates profile/episodic store
- `/forget` command → clears personal episodic memories and resets user profile to defaults
- Admin `/forget @username` → clears a specific user's personal memories

---

## Future Considerations

- **Memory aging**: Rarely-recalled memories decay in relevance score over time
- **Contradiction detection**: New memories checked against existing ones for conflicts
- **Home event memory**: Agent automatically notes significant home events (first time a device was used, patterns in usage)
