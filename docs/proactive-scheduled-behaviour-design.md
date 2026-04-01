# Proactive Scheduled Behaviour Design

## Purpose

This document proposes how HomeAgent should evolve its current scheduled-prompt
feature into a clearer, safer, and more useful proactive scheduled behaviour
system.

It is written to be easy for Codex to implement incrementally against the
current codebase.

The key constraint is:

**build on the existing `ScheduledPrompt` design rather than replacing it**

---

## Problem Statement

HomeAgent already supports scheduled prompts:

- users can create recurring or one-shot prompts
- APScheduler fires them
- the agent runs later and sends a fresh response

That is already useful, but it is still a thin wrapper around "store raw prompt
text and run it later".

That creates clear limitations:

- the system does not know *what kind* of proactive behaviour this is
- there is no structured grounding in world-model entities
- there is no first-class notion of "skip quietly if nothing changed"
- there is little delivery policy beyond "run and send"
- there is minimal run history / last-result state
- the agent cannot reason cleanly about scheduled behaviour as part of larger tasks

So the next improvement is not "add more cron strings". It is "make scheduled
behaviour structured enough to be predictable, inspectable, and composable".

---

## Current State

Today, the implementation is centered on [`ScheduledPrompt`](/Users/kristian/Documents/code/homeAgent/app/models/scheduled_prompts.py):

```text
ScheduledPrompt
├── id
├── household_id
├── user_id
├── channel_user_id
├── name
├── prompt
├── recurrence
├── time_of_day
├── run_at
├── enabled
└── created_at
```

Current runtime behavior:

1. `schedule_prompt(...)` stores a row.
2. APScheduler registers a cron/date trigger.
3. `fire_scheduled_prompt(...)` calls `run_conversation(...)` with the stored prompt text.
4. The response is delivered to the target chat.
5. One-shot prompts self-delete after firing.

This is already a good baseline and should stay compatible.

---

## Desired Outcomes

The proactive scheduled behaviour system should make the agent:

1. More useful without becoming spammy
2. More context-aware about *who* and *what* a scheduled behaviour is about
3. Better at skipping low-value runs
4. Easier to inspect, debug, and explain
5. Easier to connect to tasks and the world model later

Concretely, the system should eventually support behaviors like:

- morning briefings
- weekly calendar digests for one member
- energy summaries only when there is something notable
- follow-up nudges tied to an open task
- recurring checks that quietly skip when there is no new information

---

## Design Goals

1. Preserve backward compatibility with current `schedule_prompt`.
2. Keep the user-facing mental model simple.
3. Represent proactive behaviour as structured intent, not just raw prompt text.
4. Let proactive runs link to world-model entities.
5. Add run history and "last result" state.
6. Add delivery guardrails so proactive runs do not become noise.
7. Keep implementation incremental and local to the current architecture.

---

## Non-Goals For V1

- No autonomous self-spawning behaviour loops
- No free-form event engine
- No generic rules DSL
- No hidden side-effect execution beyond what the current policy model already allows
- No replacement of reminders or multi-step tasks

---

## Core Design Principles

### 1. Keep `ScheduledPrompt` as the compatibility layer

The existing table, scheduler registration flow, and `schedule_prompt` tool are
already valuable. New behaviour should layer on top of that model, not force an
early rewrite.

### 2. Separate "when to run" from "why this exists"

Today the schedule and the intent are both jammed into one raw prompt string.
We should keep the schedule fields, but add structured behaviour metadata so the
runtime knows what the proactive behaviour is for.

### 3. Delivery should be conditional, not automatic by default

A scheduled run is not always worth sending. The runtime should be able to:

- skip if the result is empty
- skip if nothing materially changed
- skip during quiet hours
- log *why* it skipped

### 4. Prefer world-model links over text-only references

If a behaviour is about Sondre's calendar or a specific device, store links to
canonical entities where possible instead of depending only on raw prompt wording.

### 5. Proactive behaviour should stay inspectable

Admin and logs should show:

- what was scheduled
- why it fired
- what it targeted
- whether it was delivered or skipped
- what the last result looked like

---

## Suggested Mental Model

Think of the system as:

```text
ScheduledPrompt header
        +
Structured behaviour metadata
        +
Optional links to world-model entities
        +
Run history
        +
Delivery policy
```

This is still one feature, not a second scheduler stack.

---

## Proposed Data Model

## Recommendation

Do not replace `ScheduledPrompt`. Extend it.

### Extend `ScheduledPrompt`

Suggested V1 additions:

```text
scheduled_prompt
├── id
├── household_id
├── user_id
├── channel_user_id
├── name
├── prompt
├── recurrence
├── time_of_day
├── run_at
├── enabled
├── behavior_kind          default "generic_prompt"
├── goal                   short reason, e.g. "weekly football digest"
├── config_json            structured behavior inputs
├── delivery_policy_json   quiet hours, skip-empty, dedupe rules
├── condition_json         optional preflight conditions
├── last_fired_at
├── last_delivered_at
├── last_status            "delivered" | "skipped" | "failed"
├── last_result_hash
├── last_result_preview
└── created_at
```

Notes:

- `prompt` stays for backward compatibility
- `behavior_kind` allows the runtime and UI to understand what it is
- `config_json` stores typed settings
- `delivery_policy_json` governs sending behavior
- `condition_json` governs whether the run should happen at all

### Add `ScheduledPromptLink`

This links a proactive behaviour to canonical entities.

```text
scheduled_prompt_link
├── id
├── prompt_id
├── entity_type          "member" | "calendar" | "device" | "place" | "routine" | "task"
├── entity_id
├── role                 "subject" | "source" | "target" | "focus"
└── created_at
```

Why:

- world-model grounding
- cleaner UI
- easier future task integration

### Add `ScheduledPromptRun`

This is the audit/history table.

```text
scheduled_prompt_run
├── id
├── prompt_id
├── fired_at
├── finished_at
├── status               "delivered" | "skipped" | "failed"
├── skip_reason          nullable
├── run_id               agent run id if executed
├── output_hash          nullable
├── output_preview       nullable
└── created_at
```

Why:

- observability
- dedupe / change suppression
- last-run UI
- debugging

---

## Suggested Behaviour Kinds

Keep the set small and useful.

Suggested V1 kinds:

- `generic_prompt`
- `morning_briefing`
- `calendar_digest`
- `energy_summary`
- `watch_check`
- `task_followup`

These kinds do not need separate execution engines. They mainly control:

- validation
- default delivery policy
- admin labeling
- future prompt envelopes

### `generic_prompt`

Current behavior, unchanged:

- just run the saved prompt text

### `morning_briefing`

Structured inputs could include:

- member IDs
- include calendars yes/no
- include weather yes/no
- include home anomalies yes/no

### `calendar_digest`

Structured inputs:

- member ID
- calendar IDs
- lookahead window
- whether to skip if no events

### `energy_summary`

Structured inputs:

- time window
- notable-change threshold
- whether to compare with previous period

### `watch_check`

General recurring check:

- question to ask
- linked entities
- skip if unchanged

### `task_followup`

Bridge to the multi-step task design:

- target task ID
- follow-up purpose
- whether to create a reminder vs resume a task

---

## Delivery Policy

This is the most important improvement area.

Suggested `delivery_policy_json` fields:

```json
{
  "skip_if_empty": true,
  "skip_if_unchanged": true,
  "quiet_hours_start": "22:00",
  "quiet_hours_end": "07:00",
  "max_deliveries_per_day": 1,
  "cooldown_minutes": 180,
  "priority": "normal"
}
```

### Default behaviors

- `skip_if_empty = true` for digests/checks
- `skip_if_unchanged = true` for summaries and watch-style behaviors
- quiet hours respected unless priority is high

### Important rule

The scheduler should still fire. The runtime may choose to *skip delivery* after
preflight or after the run result is evaluated.

That distinction matters for logs and later learning.

---

## Preflight Conditions

Not every prompt should even run.

Suggested `condition_json` examples:

```json
{
  "require_linked_entities": true,
  "require_calendar_data": true,
  "require_active_channel": true
}
```

Examples of preflight checks:

- prompt is enabled
- target user/channel still exists
- required linked entity still exists
- required calendar/device context is available
- not currently within a cooldown
- not already running

If preflight fails, record a skipped run with a clear reason.

---

## Proposed Runtime Flow

### 1. Scheduler fires

Same as today: APScheduler invokes the stored schedule.

### 2. Load structured behavior

Load:

- `ScheduledPrompt`
- `ScheduledPromptLink` rows
- last run metadata

### 3. Preflight evaluation

Evaluate:

- enabled?
- active channel?
- entity links still valid?
- quiet hours / cooldown?
- any required inputs missing?

Possible outcomes:

- continue to run
- skip and record why
- fail and record why

### 4. Build a proactive run envelope

Instead of passing only the raw prompt string, build a structured envelope:

```text
## Proactive Behaviour
- kind: calendar_digest
- goal: weekly football digest for Sondre
- why_now: scheduled weekly:sun at 20:00
- linked_entities:
  - member: Sondre
  - calendar: Football
- delivery_policy:
  - skip_if_empty: true
  - skip_if_unchanged: true

## Requested Prompt
What football matches does Sondre have next week? Summarize briefly.
```

This can still be passed as the `text=` argument to `run_conversation(...)`.

This is important because it improves reliability without requiring a second agent.

### 5. Run the agent

Continue to use the normal conversation agent and the current context assembly:

- profiles
- household world model
- conversation/memory layers
- tools

This keeps scheduled behavior aligned with normal household reasoning.

### 6. Post-process result

Evaluate the output:

- empty / trivial?
- same hash as last delivery?
- failed?

Possible outcomes:

- deliver
- skip as empty
- skip as unchanged
- mark failed

### 7. Persist run history

Write a `ScheduledPromptRun` row and update the prompt header:

- `last_fired_at`
- `last_delivered_at`
- `last_status`
- `last_result_hash`
- `last_result_preview`

---

## Prompt Envelope Design

This is the easiest high-value improvement.

### Why

Today the agent only sees the saved free-text prompt. It does not know:

- that this is a proactive scheduled run
- why it fired
- what delivery policy applies
- what canonical entities the run is about

### Recommendation

Keep the normal system prompt unchanged and add a structured user-text envelope
before the saved prompt content.

That keeps implementation simple and local to `fire_scheduled_prompt(...)`.

---

## Relationship To The World Model

Proactive behavior gets much better when grounded in world-model entities.

Examples:

- a `calendar_digest` linked to a `HouseholdMember` and `CalendarEntity`
- an `energy_summary` linked to a `DeviceEntity` or `WorldFact`
- a `morning_briefing` linked to one or more household members

Benefits:

- less fragile prompt wording
- clearer admin UI
- better future task handoff
- easier explanation of "why this fired"

---

## Relationship To Multi-Step Tasks

This feature should not duplicate the task system.

The relationship should be:

- multi-step tasks model user goals and progress
- proactive scheduled behavior provides timed nudges, digests, checks, or follow-ups

A proactive behavior can be:

- standalone
- linked to a task
- created as a child of a task

Good example:

- task: "Plan Sondre's football week"
- proactive behavior: Sunday 20:00 weekly football digest

The behavior helps the task ecosystem; it is not the task layer itself.

---

## Agent Tools

### Keep `schedule_prompt`

Do not remove it. It should remain the backward-compatible entry point.

In later phases, it can act as:

- a thin wrapper around `behavior_kind = "generic_prompt"`
- or a fallback when structured behavior is not appropriate

### Add structured tools later

Suggested future tools:

- `schedule_proactive_behavior(...)`
- `list_proactive_behaviors(...)`
- `cancel_proactive_behavior(...)`
- `preview_proactive_behavior(...)`

Recommended V1 behavior:

- keep only `schedule_prompt`
- improve runtime underneath it
- add structured tools after the data model exists

---

## Admin / UX Design

The admin UI should evolve from "list of scheduled prompts" to "scheduled behaviour view".

Useful columns:

- name
- kind
- schedule
- linked entities
- enabled
- last status
- last delivered
- last skip reason
- last result preview

Useful actions:

- run now
- disable
- inspect run history
- preview resolved prompt envelope

This is especially important for debugging proactive noise.

---

## Observability

Add behavior-specific events:

- `proactive.fire`
- `proactive.skip`
- `proactive.deliver`
- `proactive.fail`

Event payload fields:

- `prompt_id`
- `behavior_kind`
- `name`
- `status`
- `skip_reason`
- `duration_ms`

These should complement, not replace, existing `job.fire` / `job.complete` / `job.error`.

---

## Failure Modes And Mitigations

### 1. Proactive spam

Mitigation:

- quiet hours
- `skip_if_empty`
- `skip_if_unchanged`
- cooldowns
- per-day delivery caps

### 2. Behaviour drifts from intent

Mitigation:

- structured `goal`
- `behavior_kind`
- linked entities
- previewable run envelope

### 3. Broken links to world-model entities

Mitigation:

- preflight validation
- admin warnings
- skip with reason rather than silently sending nonsense

### 4. Prompt fires but adds no value

Mitigation:

- last-result hash
- empty/unchanged suppression
- behavior-specific defaults

### 5. Behaviour becomes a hidden task engine

Mitigation:

- keep explicit boundary with the task system
- no uncontrolled self-spawn loops
- no hidden side-effect escalation

---

## Recommended Implementation Plan

### Phase 1: Improve the existing scheduled prompt runtime

Deliver:

- `last_*` metadata on `ScheduledPrompt`
- `ScheduledPromptRun`
- output hash / preview
- skip-empty / skip-unchanged
- simple prompt envelope

This is the first high-value milestone.

### Phase 2: Add structured behavior metadata

Deliver:

- `behavior_kind`
- `goal`
- `config_json`
- `delivery_policy_json`
- `condition_json`

Still keep `schedule_prompt` as the user-facing entry point.

### Phase 3: Add world-model links

Deliver:

- `ScheduledPromptLink`
- admin display of linked entities
- stronger prompt envelope grounding

### Phase 4: Task integration

Deliver:

- optional link to task IDs
- task follow-up behavior kind
- parent/child relationship with multi-step task handling

### Phase 5: Structured user-facing creation tools

Deliver:

- `schedule_proactive_behavior(...)`
- preview and inspect tools

---

## Concrete V1 Recommendation

If choosing the most pragmatic next step for this repo:

1. Keep the current `ScheduledPrompt` table and `schedule_prompt` tool.
2. Add `behavior_kind`, `goal`, `delivery_policy_json`, and `last_*` metadata.
3. Add `ScheduledPromptRun`.
4. In `fire_scheduled_prompt(...)`, build a proactive run envelope instead of passing only raw prompt text.
5. Add output hashing and skip-empty / skip-unchanged delivery suppression.
6. Delay structured creation tools until the underlying model is stable.

This gives a large improvement in usefulness and predictability without changing
the mental model for users.

---

## Success Criteria

The design is successful when:

- scheduled behaviors are more useful and less noisy
- the system can explain why a proactive run fired
- repeated no-op digests are suppressed
- behavior can be linked to members/calendars/devices cleanly
- admins can inspect last runs and skip reasons
- the design remains compatible with the current scheduler and `schedule_prompt`
- proactive behavior can later connect cleanly to the task system and world model
