# Household World Model Design

## Purpose

This document proposes the next major architectural improvement for HomeAgent:
introducing a structured household world model alongside the current profile +
episodic-memory system.

It was originally written as an implementation-oriented design suggestion.

## Status

Large parts of this design are now implemented in the codebase:

- world-model tables in `users.db`
- startup bootstrap from users, calendars, Homey structure, and seed facts
- compact `## Household Model` prompt injection
- admin world-model browser and edit endpoints
- agent world-model read/write tools

What remains design-oriented in this document is mostly the later-stage proposal
pipeline, broader autonomy hooks, and future refinement ideas.

---

## Wanted Outcomes

The world model should make the agent:

1. More useful in day-to-day household coordination
2. More autonomous without becoming unsafe
3. More consistent across conversations and users
4. Less dependent on prompt wording and fragile text retrieval
5. Better at planning with household structure, not just recalled sentences

Concretely, after this work the agent should be able to:

- Reliably understand relationships like person -> calendars -> routines -> devices -> rooms
- Answer household questions using structured facts before falling back to fuzzy memory
- Ground tool calls and planning in canonical entities instead of free-text guesses
- Preserve important household knowledge even when conversation history is compacted
- Support future autonomy features like delegated tasks and event-driven actions on top of a stable model

---

## Current State

Today the agent has four main knowledge inputs:

- Static prompt text from `prompts/persona.md` and `prompts/instructions.md`
- Profiles from `memory.db`
- Episodic text memories retrieved by semantic search
- Recent conversation history + conversation summary

This works, but it has clear limits:

- Important household structure is mostly represented as text, not as typed entities
- The agent must infer relationships from prose instead of querying a canonical model
- Some intended context is documented but not fully wired into runtime prompt assembly
- Memory retrieval is relevance-based, which is good for recall but weak for durable structure
- Tool selection and planning are not grounded in a reusable representation of the household

Relevant current files:

- `app/agent/agent.py`
- `app/agent/context.py`
- `app/memory/profiles.py`
- `app/memory/episodic.py`
- `app/agent/tools/calendar.py`
- `app/homey/home_profile.py`
- `prompts/home_context.md`

---

## Problem Statement

HomeAgent currently remembers facts mostly as:

- profile JSON blobs
- episodic text sentences
- prompt text

That is enough for recall, but not enough for durable reasoning about the home as a system.

Examples of facts that should be structured, not only remembered as text:

- "Sondre" is a household member
- Sondre has a football calendar
- football practice usually implies transport planning
- the "kontor" light is in the upstairs office
- the hallway smart plug represents whole-house power
- night mode means lights off, but heating unchanged

These are not just memories. They are relationships that should be queryable and reusable.

---

## Design Goal

Add a structured world model layer that complements existing memory, rather than replacing it.

The new layer should hold canonical household entities, activities, interest/goals, devices and their relationships.
The agent should use this layer for:

- grounding
- planning
- tool disambiguation
- context assembly
- future autonomous behavior

The episodic memory layer should continue to exist for soft recall, nuance, and user-specific detail.

---

## Design Principles

1. Structured facts should win over inferred text when both exist.
2. The model must be additive, not a rewrite of the current memory system.
3. Writes must be conservative. Prefer explicit user statements and trusted system sources.
4. The model must remain inspectable from the admin UI.
5. The agent should read the model by default, but write to it only through controlled paths.
6. The model should support future eventing and task orchestration without redesign.

---

## Proposed Architecture

Introduce a new layer: `World Model`.

```text
User message / scheduled prompt / future event
        │
        ▼
Context assembly
        │
        ├── Profiles
        ├── Conversation summary
        ├── Episodic memories
        ├── World model snapshot / relevant entities
        └── Recent messages
        │
        ▼
Agent run
        │
        ├── Reads world model directly in context
        ├── Uses tools grounded by canonical entities
        └── Emits candidate updates
        │
        ▼
Post-run update pipeline
        ├── Memory extraction
        ├── World-model extraction / reconciliation
        └── Admin review hooks for risky updates
```

The world model should sit conceptually between:

- profiles and episodic memory
- live Homey/calendar state
- agent planning/tool use

---

## Data Model

Use a normalized relational model in SQLite first. Do not start with a graph database.

Suggested initial entities, but consider to add at once for users interest and goals and users activities from start, same style:

### 1. HouseholdMember

Canonical people in the household.

Fields:

- `id`
- `household_id`
- `name`
- `aliases_json`
- `role`
- `timezone`
- `is_active`
- `source`
- `created_at`
- `updated_at`

### 2. Place

Rooms, floors, zones, outdoor areas.

Fields:

- `id`
- `household_id`
- `name`
- `aliases_json`
- `kind` (`room`, `floor`, `zone`, `outdoor`)
- `parent_place_id`
- `source`
- `created_at`
- `updated_at`

### 3. DeviceEntity

Canonical device metadata derived from Homey + household naming.

Fields:

- `id`
- `household_id`
- `external_device_id`
- `name`
- `aliases_json`
- `device_type`
- `place_id`
- `capabilities_json`
- `is_controllable`
- `source`
- `created_at`
- `updated_at`

### 4. CalendarEntity

Registered calendars as first-class household objects.

Fields:

- `id`
- `household_id`
- `external_calendar_id`
- `name`
- `member_id`
- `category`
- `source_url`
- `is_active`
- `created_at`
- `updated_at`

### 5. RoutineEntity

Structured household routines and operational meanings.

Examples:

- night mode
- school pickup window
- football practice transport

Fields:

- `id`
- `household_id`
- `name`
- `description`
- `kind`
- `schedule_hint_json`
- `created_at`
- `updated_at`

### 6. Relationship

Generic typed links between entities.

Fields:

- `id`
- `household_id`
- `subject_type`
- `subject_id`
- `predicate`
- `object_type`
- `object_id`
- `metadata_json`
- `confidence`
- `source`
- `created_at`
- `updated_at`

Example predicates:

- `member_has_calendar`
- `member_uses_device`
- `device_in_place`
- `place_contains_place`
- `routine_applies_to_member`
- `routine_affects_place`
- `alias_of`
- `device_represents_metric`

### 7. WorldFact

For structured facts that do not fit neatly as entities or relationships but should still be canonical.

Fields:

- `id`
- `household_id`
- `scope`
- `key`
- `value_json`
- `source`
- `confidence`
- `created_at`
- `updated_at`

Examples:

- `night_mode.lights = off`
- `night_mode.heating = unchanged`
- `default_language = no`

---

## Storage Recommendation

Keep the world model in `users.db`, not `memory.db`.

Reasoning:

- It represents operational household structure, not conversational recall
- It will be read by scheduling, planning, Homey grounding, and admin tooling
- Calendars and scheduled prompts already live in `users.db`, which is closer to durable household configuration

Do not overload profile tables for this. Create dedicated tables.

---

## Sources of Truth

Each record should track `source`.

Initial trusted sources:

- `user_explicit` — user clearly stated the fact
- `admin_authored` — added or corrected from admin UI
- `homey_import` — imported from Homey topology
- `calendar_import` — imported from calendar registrations
- `migration_seed` — bootstrapped from existing prompt/profile data
- `agent_inferred` — candidate inference, lower confidence

Priority order when sources conflict:

1. `admin_authored`
2. `user_explicit`
3. `homey_import` / `calendar_import`
4. `migration_seed`
5. `agent_inferred`

The runtime should prefer higher-priority sources.

---

## Read Path

Add world-model retrieval to `assemble_context()`.

Suggested behavior:

1. Detect likely relevant entities from current user message
2. Load a compact structured snapshot for those entities
3. Always include a small household baseline:
   - household members
   - important places
   - key routines
   - known naming aliases
4. Include richer slices only when relevant:
   - person-specific routines
   - device mappings
   - calendar-to-member links

The output should be formatted for the LLM as compact structured markdown, not raw JSON.

Example:

```text
## Household Model

Members:
- Kristian (admin)
- Sondre

Places:
- Basement
- First floor
- Second floor
- Office (alias: kontor) -> Second floor

Known mappings:
- "stue" = living room
- hallway smart plug = total house power

Relevant relationships:
- Sondre -> calendar: "Sondre Football"
- Office light -> place: Office
- Night mode -> lights off, heating unchanged
```

Important: keep this section deterministic and compact. This is grounding context, not prose.

---

## Write Path

There should be three write paths.

### 1. Explicit tool-driven writes

Add dedicated world-model tools for safe structured updates.

Suggested tools:

- `list_world_entities`
- `get_world_model_snapshot`
- `upsert_world_fact`
- `link_world_entities`
- `rename_world_alias`

These should be used sparingly and mostly when the user explicitly corrects or defines structure.

### 2. Trusted system sync

Background sync jobs should populate or refresh:

- Homey devices and zones
- registered calendars
- household members from users table

This should create or update canonical entities without requiring the LLM.

### 3. Candidate extraction + reconciliation

A background model can propose world-model updates after runs, but these should not auto-apply broadly at first.

Recommended v1:

- auto-apply only low-risk alias/routine facts with high confidence
- store all other proposals in a review table
- expose them in admin UI for approval

---

## Reconciliation Model

Add a `WorldModelProposal` table for suggested changes.

Fields:

- `id`
- `household_id`
- `proposal_type`
- `payload_json`
- `reason`
- `source_run_id`
- `status` (`pending`, `accepted`, `rejected`)
- `created_at`
- `reviewed_at`

This avoids letting the agent silently rewrite household structure based on one ambiguous sentence.

---

## Integration With Existing Components

### Agent

Update `app/agent/context.py` to assemble:

- world model baseline
- relevant entity slices
- entity aliases

Update `app/agent/agent.py` to inject this in a dedicated `## Household Model` section.

### Memory

Do not remove episodic memory.

Instead:

- use world model for canonical structure
- use episodic memory for nuance and soft recall
- optionally promote repeated stable episodic facts into world-model proposals

### Homey

Use the world model to improve:

- alias resolution
- room inference
- device disambiguation
- explanation quality

### Calendars

Link calendars to canonical members directly rather than relying only on free-text `member_name`.

### Admin

Extend admin with:

- world model browser
- proposal review queue
- entity search
- relationship inspector

---

## Migration Plan

### Phase 1: Introduce read-only model bootstrap

Build tables and populate from trusted existing data:

- users -> HouseholdMember
- calendars -> CalendarEntity
- Homey structure -> Place + DeviceEntity
- selected `home_context.md` conventions -> WorldFact seed

At this phase the model is mostly system-generated and read-only.

### Phase 2: Inject into runtime context

Update context assembly to include compact world-model grounding.

Expected result:

- better consistency in naming
- better device/location disambiguation
- more stable reasoning about routines and relationships

### Phase 3: Add explicit write tools and admin editing

Allow controlled corrections and additions.

Examples:

- "Remember that the hallway smart plug is total house power"
- "kontor means the upstairs office"
- "Night mode keeps the heating the same"

### Phase 4: Add proposal pipeline

Let background extraction propose structured updates from conversation history.

Start conservative.

---

## Non-Goals For V1

- No graph database
- No fully autonomous self-editing of the household model
- No attempt to structure every conversational memory
- No deep ontology design
- No large generic knowledge graph outside the household domain

The first version should stay narrow and operational.

---

## Risks

### 1. Over-structuring too early

If the schema is too ambitious, it will become brittle and expensive to maintain.

Mitigation:

- keep entity types few
- prefer generic relationships
- add fields only when the runtime truly needs them

### 2. Agent writes incorrect structure

If inferred updates auto-apply too freely, the model becomes less trustworthy than memory.

Mitigation:

- source tracking
- confidence scoring
- admin review for non-trivial proposals

### 3. Prompt bloat

If too much model data is injected every run, token usage rises and quality drops.

Mitigation:

- compact baseline
- relevance-based entity slice selection
- deterministic formatting

### 4. Duplication with profiles and episodic memory

Without clear boundaries, the same fact ends up in three places.

Mitigation:

- document canonical ownership
- structured facts in world model
- personal preferences in profiles
- nuanced recall in episodic memory

---

## Success Criteria

The design is successful when all of the following are true:

- The agent resolves household aliases and room/device references more reliably
- Calendar, people, place, and device relationships are visible in a single inspectable model
- The agent can explain why it chose a device or interpretation using model-backed facts
- New autonomy features can target canonical entities instead of raw text
- Admin can inspect and correct the model without editing prompts or DB rows manually
- Prompt quality improves without materially increasing prompt size
- the agent can be more proactive and helpful based on its understanding of the the household and persons
- the users expectations are better understood and used by the agent

---

## Suggested Implementation Order

1. Schema + models for places, devices, members, calendars, relationships, world facts
2. Sync/bootstrap from users, calendars, and Homey topology
3. Context assembly read path with compact formatter
4. Admin read-only inspection UI
5. Explicit update tools
6. Proposal pipeline for inferred updates

---

## Concrete First Deliverable

If implementing this incrementally, the first useful milestone should be:

"HomeAgent has a read-only household world model that is automatically built from users, calendars, Homey structure, and seeded household facts, and a compact `## Household Model` section is injected into every relevant agent run."

That milestone is large enough to improve usefulness immediately, while staying low-risk.


## Tech
We continue with SQL lite for now, but might move to postgres or similar in the future. Hence, putthe world-model access behind clean repository/service boundaries so a later move is possible.
