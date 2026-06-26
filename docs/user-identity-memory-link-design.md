# User Identity And Memory Link Design

Status: implemented core identity/memory link, with future channel extensions
still relevant
Last code check: 2026-06-26
Runtime entry points: `app/models/users.py`, `app/memory/`,
`app/agent/context.py`, `app/commands/handlers.py`

## Purpose

Tighten the link between Telegram users, future purpose-specific email
identities, HomeAgent users, household members, and memory so onboarding a new
household member does not create vague or detached personal memories.

The same identity link is also needed for user-scoped features such as flight
tracking, scheduled prompts, reminders, and future email-triggered workflows.
Telegram remains the primary interactive channel. Email is an additional
incoming/outgoing channel for specific purposes, such as receiving flight booking
emails or sending selected travel updates.

This is a small corrective design, not a full memory redesign.

## Current State

Telegram identity is currently mapped like this:

```text
Telegram user id
  -> User.telegram_id
  -> User.id
  -> HouseholdMember.user_id
```

There is also a generic `ChannelMapping` table:

```text
ChannelMapping(user_id, channel, channel_user_id)
```

That table should become the forward-compatible source for additional channel
identities:

```text
telegram:<telegram_user_id> -> User.id
email:<normalized_email>    -> User.id
```

`User.telegram_id` remains required for Telegram-backed users and should continue
to be used for the primary Telegram flow. Additional channel identifiers, such as
email addresses, should be represented in `ChannelMapping` and resolve back to
the same `User.id`.

The runtime already uses `User.id` for:

- per-user conversation history
- per-user conversation summary
- per-user profile
- personal episodic memories
- per-user run lock
- task ownership

The world model already links the current speaker by
`HouseholdMember.user_id == current_user_id` and renders that member as
`<- speaking` in the household model.

Email channel is not yet implemented, but coming soon for selected workflows.
The intent is one designated forwarding address per user: the user forwards or
sends relevant emails (e.g. flight booking confirmations) to the agent's
address, and the agent identifies the sender via `ChannelMapping`. The agent
does not scan or monitor any email inbox. Flight tracking is user-scoped, so
forwarded booking emails and Telegram conversations must resolve to the same
`User.id` for the same household member.

Current deployment reality:

- there is one actual human user today
- there is one Telegram user linked today
- that user is Kristian
- existing personal profiles, conversations, summaries, tasks, and episodic
  memories should therefore be treated as Kristian's data

## Problem

The link is technically present, but too implicit for onboarding and memory.

Gaps:

- New users are created without `onboarding_complete` set, so there is no
  durable signal that a name has been user-asserted.
- There is no clear onboarding command/flow that updates both `User.name` and
  the linked `HouseholdMember.name`.
- `UserProfile` is keyed by `user_id`, but not explicitly linked to the
  household member.
- `EpisodicMemory` is keyed by `user_id` for personal memories, but not by
  `member_id`.
- Auto-extracted memories are stored under the speaking `user_id`, even when the
  extracted fact semantically describes another household member.
- Memory text may say "the user prefers..." instead of "Kristian prefers...",
  which becomes ambiguous after more household members are active.

## Design Goals

1. Make onboarding explicit and testable.
2. Keep Telegram as the primary interactive channel.
3. Treat email as an additional purpose-specific channel, not as a replacement
   for Telegram.
4. Store additional channel identifiers through `ChannelMapping` so future
   channels can resolve to the same `User.id`.
5. Keep `User.id` as the durable account identity.
6. Make `HouseholdMember` the canonical person entity.
7. Improve memory semantics without rewriting the whole memory system.
8. Avoid leaking one member's personal memories to another member.

## Non-Goals

- No multi-household identity redesign.
- No email ingestion/channel implementation in this change.
- No email-based auto-onboarding in V1.
- No replacement of Telegram as the primary user channel.
- No automatic inference of family relationships during onboarding.
- No admin UI redesign required for V1.
- No broad rewrite of the memory extractor.

## Proposed V1 Changes

### 1. Add Explicit Identity Onboarding

Add a command:

```text
/me
```

Supported forms:

```text
/me
/me show
/me name Kristian
/me email kristian@example.com
/me email remove kristian@example.com
```

Behavior:

- `/me` shows help for the command.
- `/me show` shows known and linked identity data for the current user.
- `/me name <display name>` updates the user's display name and linked member.
- `/me email <address>` links an email address to the current Telegram-backed
  user for future purpose-specific email workflows.
- `/me email remove <address>` removes an email mapping for the current user.

Name update behavior:

1. Resolve the current `User` from Telegram ID.
2. Update `User.name` and set `User.onboarding_complete = True`.
3. Upsert linked `HouseholdMember` by `user_id`.
4. Update `HouseholdMember.name` and mark `name_user_asserted = True`.
5. Set `HouseholdMember.role` from `User.is_admin`.
6. Upsert `UserProfile.name = <name>`.
7. Return a short confirmation.

User-asserted names are sticky: world model sync from Homey or other sources
must not overwrite `HouseholdMember.name` when `name_user_asserted = True`.
This flag lives on `HouseholdMember` and is set only by `/me name`.

Email update behavior:

1. Resolve the current `User` from Telegram ID.
2. Normalize email address by trimming whitespace and lowercasing the domain and
   local part. V1 does not attempt provider-specific alias normalization such as
   Gmail dot/plus handling.
3. Reject obviously invalid addresses.
4. Check `ChannelMapping(channel="email", channel_user_id=<normalized_email>)`.
5. If mapped to another user, reject and tell the user to ask an admin.
6. Otherwise create or update the mapping for the current user.
7. Upsert `UserProfile.email = <normalized_email>`.
8. Return a short confirmation.

Example response:

```text
Updated your profile: Kristian.
```

Example `/me show` response:

```text
You are Kristian.
Telegram id: 123456789
Email: kristian@example.com
Role: admin
World model member: Kristian
```

Do not show email addresses for other household members through `/me`.

### 2. On First Message, Append Soft Onboarding Prompt

When an allowlisted Telegram user first appears and `User.onboarding_complete`
is `False`, the bot should nudge the user to set their name without blocking
the normal agent run.

Behavior:

- create `User` and linked `HouseholdMember` as today
- call `get_member_for_user(household_id, user_id)` to resolve or create the
  linked member
- run the agent normally
- append a soft onboarding note to the response:

```text
Tip: send /me name Your Name to identify yourself so I can personalise responses.
```

This is a one-time nudge: only append it when `User.onboarding_complete` is
`False`. Once `/me name` is called, `onboarding_complete` becomes `True` and
the nudge stops. No name-based checks (`name == "User"`) and no hard block on
normal conversation.

### 3. Add Current Member Context

Extend context assembly with a compact identity section:

```text
## Current User
- user_id: <uuid>
- name: Kristian
- household_member_id: <uuid>
- household_member_name: Kristian
- role: admin
```

This is not for exposing secrets. It is grounding for the model so it can phrase
personal memory correctly and distinguish:

- "I prefer short answers" -> current member
- "Sondre likes football" -> another member / world model member

Do not include Telegram ID in model context unless explicitly needed for
debugging. Telegram ID is channel metadata, not household identity.

### 4. Make Personal Memory Text Member-Specific

Update memory tool guidance and extraction prompt rules:

- Personal memories should name the member when known:
  - good: `Kristian prefers concise technical answers.`
  - weak: `The user prefers concise technical answers.`
- Household memories should avoid claiming a member unless the speaker is clear.
- Facts about another known member should prefer `update_world_model` for
  interests, activities, goals, aliases, routines, and stable relationships.

This is a low-risk prompt/tool-guidance change and does not require schema
changes.

### 5. Add Optional `member_id` To EpisodicMemory

Add nullable `member_id` to `EpisodicMemory`.

```python
member_id: str | None = None
```

Meaning:

- `user_id != None`, `member_id != None`: personal memory for a Telegram-backed
  household member.
- `user_id == None`, `member_id == None`: household-wide memory.
- `user_id == None`, `member_id != None`: shared household memory about a
  specific member.

V1 storage behavior:

- For `scope="personal"`, set both `user_id=ctx.deps.user_id` and
  `member_id=current_member.id` when available.
- For `scope="household"`, keep `user_id=None`; set `member_id` only if the
  memory is explicitly about a named member and the member can be resolved.
- Retrieval:
  - personal memories: current user's `user_id`
  - household memories: `user_id IS NULL` — including household memories where
    `member_id` matches the current member (these are shared facts about the
    person, not private memories belonging to another account)
  - do not retrieve another user's personal memories (`user_id` belongs to
    someone else) even if `member_id` matches

This gives future tools and admin views a clean member link without weakening
privacy.

## Data Model Changes

### `ChannelMapping`

Use existing `ChannelMapping` for Telegram compatibility mappings and additional
email mappings:

```python
user_id: str
channel: str              # "telegram" | "email" | future channels
channel_user_id: str      # str(telegram_id) or normalized email address
```

Required behavior:

- add/enforce uniqueness for `(channel, channel_user_id)`
- create a `telegram` mapping for existing/new Telegram users
- create an `email` mapping when `/me email <address>` is used
- keep `User.preferred_channel` for general outbound default, but allow
  workflow-specific routing to choose email where appropriate

Current code has `User.telegram_id` as a required unique field. That is fine for
this design: users remain Telegram-backed. Email mappings attach to those users
and are used only to route purpose-specific email events or messages.

### `EpisodicMemory`

Add:

```python
member_id: str | None = Field(default=None, index=True)
```

No SQLite FK is needed because memory and users/world-model tables live in
different databases. Treat it like the existing `user_id` cross-db reference.

### `User`

Add:

```python
onboarding_complete: bool = Field(default=False)
```

Set to `True` by `/me name`. Used to suppress the soft onboarding nudge after
the first successful name assertion. Not used as an access gate.

### `HouseholdMember`

Add:

```python
name_user_asserted: bool = Field(default=False)
```

Set to `True` by `/me name`. Prevents world-model sync from Homey or other
sources from overwriting the name the user explicitly set.

### Existing Models Used As-Is

No change needed:

- `User.name`
- `HouseholdMember.user_id`
- `UserProfile.user_id`

Keep but treat as Telegram compatibility field:

- `User.telegram_id`

## Migration Strategy

This change touches both `users.db` and `memory.db`, so split schema migration
from identity backfill.

### Users DB Migration

Add:

- `User.onboarding_complete`, default `False`
- `HouseholdMember.name_user_asserted`, default `False`
- unique index or constraint on `ChannelMapping(channel, channel_user_id)`

Backfill:

- for every existing `User`, create
  `ChannelMapping(channel="telegram", channel_user_id=str(User.telegram_id))`
  if it does not already exist
- before adding the unique constraint, deduplicate any accidental duplicate
  `ChannelMapping` rows by keeping the oldest row for each `(channel,
  channel_user_id)`

Existing users should not be marked `onboarding_complete=True` automatically.
The current user should run `/me name Kristian` once to make the name
user-asserted and stop the soft onboarding nudge.

### Memory DB Migration

Add:

- `EpisodicMemory.member_id`, nullable, indexed

Do not try to do the full memory backfill inside a normal memory-db-only Alembic
migration. The backfill needs to read `users.db` / world-model members and write
`memory.db`, so it should be an application-level migration helper or startup
maintenance job that runs after both schema migrations are applied.

Backfill behavior:

1. For each personal memory where `EpisodicMemory.user_id IS NOT NULL` and
   `member_id IS NULL`, resolve `HouseholdMember` by
   `HouseholdMember.user_id == EpisodicMemory.user_id`.
2. If found, set `EpisodicMemory.member_id = HouseholdMember.id`.
3. Leave household memories (`user_id IS NULL`) unchanged unless a later
   semantic classifier explicitly links them to a member.
4. Emit a count of updated/skipped memories.

For the current single-user deployment, this means existing personal memories
will be linked to Kristian after `/me name Kristian` updates the existing
Telegram-backed `User` and linked `HouseholdMember`. The memories are not
"transferred" to a new user; they stay on the same `User.id` and receive the
new `member_id` metadata.

Because there is only one real Telegram-backed user today, the backfill can be
strict and simple:

1. Assert there is exactly one `User` row, or exactly one active
   Telegram-backed user intended for migration.
2. Assert that user is the current Kristian account.
3. Resolve/create the linked `HouseholdMember`.
4. Set `member_id` on all existing personal `EpisodicMemory` rows for that
   `User.id`.
5. Do not alter `user_id`, conversation rows, summaries, tasks, or profiles.

If the assertion fails, skip the backfill and emit/log a warning instead of
guessing.

### Current Identity Preservation

Do not create a new `User` for Kristian if the existing Telegram ID already has
a `User` row. `/me name Kristian` should update that existing row. Because
profiles, conversations, summaries, tasks, and personal memories are keyed by
`User.id`, they continue to belong to the same person after the rename.

For Claude review: the important invariant is that Kristian remains the existing
`User.id`. Implementation must not create a second user named Kristian and must
not move memory rows between users.

### World Model Sync Guard

`WorldModelRepository.upsert_member(...)` and startup world-model sync must
respect `HouseholdMember.name_user_asserted=True`. When an existing member has
that flag, sync may update role/source/timestamps if needed, but must not
overwrite the member name.

## Runtime Flow

### First Allowlisted Telegram Message

```text
Telegram id
  -> allowlist check
  -> get/create User by User.telegram_id
  -> create/update ChannelMapping(channel="telegram", channel_user_id=str(telegram_id))
  -> get_member_for_user(household_id, User.id)  # resolve or upsert HouseholdMember
  -> run agent
  -> if not User.onboarding_complete: append soft onboarding nudge to response
```

### `/me name Kristian`

```text
Telegram id
  -> User
  -> update User.name
  -> upsert HouseholdMember(user_id=User.id, name="Kristian")
  -> upsert UserProfile.name
  -> confirm
```

### `/me email kristian@example.com`

```text
Telegram id
  -> current User
  -> normalize email
  -> ensure no other user owns ChannelMapping(email, normalized_email)
  -> upsert ChannelMapping(user_id=User.id, channel="email", channel_user_id=normalized_email)
  -> upsert UserProfile.email
  -> confirm
```

### Future Email Inbound Resolution

When email channel is implemented for flight schedule parsing or similar
workflows:

```text
Inbound email
  -> trusted mailbox/source validation
  -> normalize sender address
  -> lookup ChannelMapping(channel="email", channel_user_id=normalized_sender)
  -> if found: process workflow under mapped User.id
  -> if not found: do not auto-create user; drop, ignore, or route to admin review
```

Email sender address alone is not a strong authentication factor. Do not let an
unknown email sender create a new HomeAgent user or access personal memory. Email
mappings must be created through an already-authenticated Telegram user via
`/me email ...` or through an admin tool.

Email is a forwarding channel, not an inbox. The user forwards specific emails
(e.g. flight booking confirmations) to the agent's designated address. The
agent identifies the sender from the forwarded email's `From` header via
`ChannelMapping`, then processes only the specific workflow the email describes
(e.g. create/update a flight watch under the mapped `User.id`). It does not
treat forwarded emails as general conversation or add them to episodic memory.

### Normal Agent Run

```text
User.id
  -> assemble_context()
  -> load UserProfile(user_id)
  -> load HouseholdMember(user_id)
  -> render Current User section
  -> render Household Model with current member marked as speaking
  -> retrieve personal + household memories
```

## Memory Extraction Behavior

Keep V1 simple:

1. Auto-extracted facts from a user's conversation still default to personal
   memory for that `user_id`.
2. If current member is known, also store `member_id`.
3. Extraction prompt should avoid "the user" when it can use the current member
   name.
4. If the extracted fact is clearly structured world-model content, prefer
   world-model proposal/update path later. Do not build this classifier in V1
   unless needed.

## Admin And Observability

Emit events:

| Event | Meaning |
| --- | --- |
| `identity.user_created` | New allowlisted Telegram user created |
| `identity.member_linked` | User linked/upserted to HouseholdMember |
| `identity.profile_updated` | `/me` updated User/UserProfile/member name |
| `identity.channel_linked` | `/me` linked Telegram/email channel mapping |
| `identity.channel_unlinked` | `/me` removed an email channel mapping |
| `memory.member_linked` | Stored memory with `member_id` |

Admin display can remain minimal for V1. Useful later:

- show Telegram-backed users
- show linked household member
- show placeholder users that still need `/me`
- show count of personal memories by user/member

## Privacy Rules

- Telegram ID stays out of prompt context by default.
- Email address stays out of prompt context by default unless the user explicitly
  asks about their configured contact details or a purpose-specific email
  workflow needs it.
- Channel identifiers are routing metadata, not durable household identity.
- Personal memories remain scoped by `user_id`.
- `member_id` is metadata, not a permission grant.
- Another member should not receive personal memories where `user_id` belongs to
  someone else.
- Household-wide memories about a member may be retrieved by all household
  members, because they are stored with `user_id=None`.
- Future email inbound must resolve only through verified `ChannelMapping`
  entries; unknown senders must not get access to the agent or memories.
- Email-derived workflow events should be narrow and typed, e.g.
  `flight_booking_received`, not generic "user message" events.

## Implementation Steps

1. Add `User.onboarding_complete` and `HouseholdMember.name_user_asserted`
   fields and Alembic migration.
2. Add current-member resolver helper:
   `get_member_for_user(household_id, user_id)`.
3. Add `/me` command (`/me`, `/me show`, `/me name <name>`).
4. Update first-message flow: call `get_member_for_user`, append soft
   onboarding nudge when `onboarding_complete` is `False`.
5. Ensure new/existing Telegram users get a
   `ChannelMapping(channel="telegram", channel_user_id=str(telegram_id))`.
6. Add `/me email <address>` and `/me email remove <address>`.
7. Add current-user context section in `assemble_context()`.
8. Update memory tool and extractor prompts to use member-specific wording.
9. Add nullable `member_id` to `EpisodicMemory` and Alembic migration.
10. Set `member_id` on personal memories when current member is known.
11. Backfill existing personal memories with `member_id` for the current
    single-user deployment (all existing memories belong to Kristian).
12. Add focused tests for onboarding, `/me`, context rendering, and memory
    visibility.

## Tests

Minimum test coverage:

- allowlisted new Telegram ID creates `User` and linked `HouseholdMember`
- allowlisted new Telegram ID creates `ChannelMapping(channel="telegram")`
- user with `onboarding_complete=False` receives soft onboarding nudge appended
  to normal agent response; agent response is not blocked
- `/me name Kristian` updates `User.name`, `HouseholdMember.name`, `UserProfile`,
  sets `User.onboarding_complete=True` and `HouseholdMember.name_user_asserted=True`
- `/me email kristian@example.com` creates an email `ChannelMapping`
- `/me email ...` rejects an email already mapped to another user
- `/me email remove kristian@example.com` removes only the current user's mapping
- email normalization is deterministic and case-insensitive
- `format_world_model(..., current_user_id=...)` marks the right member as
  speaking
- personal memory stored for current user includes `member_id`
- another user cannot retrieve personal memories from that user
- household memory with `member_id` remains visible household-wide

## Resolved Decisions

1. `/me` is user-editable forever, with admin visibility. No lockout after
   first setup.
2. Display names only (not full legal names).
3. No hard block on missing onboarding. Soft nudge appended to normal response
   when `onboarding_complete` is `False`. No `name=="User"` check.
4. Existing memories can be backfilled with `member_id` in V1 since the
   current deployment has a single actual Telegram-backed user: Kristian. The
   backfill should assert that before updating rows.
5. `/me email` requires no admin approval when issued by an authenticated
   Telegram user for their own account. Conflicts are rejected and all mappings
   are visible to admin.
6. Email-only users are not supported. Email is a forwarding channel only:
   users forward specific emails to the agent's address; the agent identifies
   the sender via `ChannelMapping` and processes the specific workflow.
   Users stay Telegram-backed.
7. User-asserted names (set via `/me name`) are sticky. World model sync must
   not overwrite `HouseholdMember.name` when `name_user_asserted = True`.

## Current Judgement

Implementing `/me`, `ChannelMapping` for email mappings, current-member context,
and `EpisodicMemory.member_id` is the smallest useful step. Onboarding is a
soft nudge, not a gate — existing conversations are not blocked and no
name-based heuristics are used. Email is a forwarding channel: the user sends
specific emails to the agent's address; the agent identifies the sender and
processes the narrowly-scoped workflow. Existing personal memories can be
backfilled with `member_id` for the current single-user deployment. The design
keeps Telegram as the primary user channel without weakening current privacy
boundaries or requiring a broad memory rearchitecture.

## Claude Review Checklist

Ask Claude to check:

- Does the migration preserve the existing Kristian `User.id`?
- Does the backfill correctly avoid moving or duplicating memories?
- Are the single-user assumptions explicit enough and guarded by assertions?
- Is `member_id` clearly metadata rather than a permission rule?
- Does `name_user_asserted` prevent world-model sync from overwriting `/me name`
  without blocking legitimate role/source updates?
- Is email correctly scoped as a forwarding/workflow channel, not a replacement
  identity system?
