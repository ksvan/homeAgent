# Multi-Step Task Handling Design

## Purpose

This document proposes a concrete approach for turning HomeAgent's current `Task`
record from a scheduler-backed container for reminders/actions into a real
multi-step task orchestration layer.

The goal is not to build a generic workflow engine. The goal is to make the
agent reliably handle household tasks that take more than one turn, more than
one tool call, or more than one point in time.

Examples:

- "Help me plan Sondre's football week"
- "Figure out who needs pickup on Thursday and remind me tomorrow morning"
- "Find three dinner options, then wait for me to choose one"
- "Track this issue and remind me if I haven't done it by Sunday"

---

## Current State

Today, `Task` already exists in [`app/models/tasks.py`](/Users/kristian/Documents/code/homeAgent/app/models/tasks.py):

```text
Task
├── id
├── household_id
├── user_id
├── title
├── status
├── steps           JSON array
├── current_step
├── context         JSON object
├── trigger_event_id
├── created_at
├── updated_at
└── completed_at
```

Current runtime usage is narrower than the schema suggests:

- reminders are stored as `Task`
- scheduled Homey actions are stored as `Task`
- task state is not injected into normal conversation context
- there is no dedicated task manager/orchestrator service yet
- there are no agent tools for creating/resuming/completing general tasks

So the design problem is not "invent tasks from zero". It is "promote the
existing task substrate into a real conversational orchestration layer without
breaking reminders/actions".

---

## Design Goals

1. Support resumable work across multiple user turns.
2. Let the agent pause cleanly for user input, confirmation, or time-based waits.
3. Keep the state inspectable and repairable by humans.
4. Ground task context in canonical world-model entities where possible.
5. Avoid duplicate execution when a user follows up or messages quickly.
6. Preserve a strict distinction between safe planning and risky side effects.
7. Start simple enough that V1 ships without a large workflow DSL.

---

## Non-Goals For V1

- No BPMN-style workflow language
- No arbitrary branching graph editor
- No generic multi-agent planner/executor architecture
- No hidden autonomous task spawning without explicit user or scheduled trigger
- No full background worker fleet beyond the existing scheduler

---

## Core Design Principles

### 1. Task state should be compact and typed enough to survive conversation drift

Free-form conversation summaries are useful, but they are not enough for
resumption. The task layer needs explicit fields for status, next step, and the
minimum machine-readable context needed to continue.

### 2. The model should suggest work, but the runtime should own state transitions

The LLM can decide:

- whether a task is needed
- what the next step is
- whether it is blocked on user input

But the runtime should still own:

- status transitions
- persistence
- scheduled wakeups
- confirmation handoff
- idempotency / duplicate suppression

### 3. Task context should prefer world-model IDs over raw text

If a task is about Sondre, a football calendar, and the upstairs office light,
the stored task context should reference canonical IDs or stable keys when
possible, not only prose like "Sondre's football thing upstairs".

### 4. A task is not the same thing as a scheduled job

Scheduled prompts, reminders, and delayed actions are useful, but they are only
one kind of wait state. The task layer should model the user's goal; scheduler
entries are just one execution mechanism underneath it.

---

## What Counts As A Multi-Step Task

HomeAgent should create a multi-step task when all of the following are true:

- the user goal plausibly spans multiple turns, tools, or times
- partial progress must be remembered explicitly
- the agent may need to wait for input or a future moment before continuing

Typical triggers:

- planning
- comparison and choice
- gathering then summarizing
- anything that says "later", "after", "when", "remind me if", "wait until"
- anything that the agent cannot finish correctly in a single run without losing state

Do **not** create a general task for:

- one-shot factual answers
- single immediate tool actions
- pure chat with no durable intermediate state

---

## Task Types

Use a small number of coarse task kinds rather than dozens of narrow ones.

Suggested V1 task kinds:

- `plan`: gather options, compare, recommend, wait for user choice
- `track`: monitor an issue or commitment over time
- `prepare`: collect information for a future time-bound need
- `handoff`: queue a future reminder/action/prompt as part of a larger task
- `admin`: internal operational tasks created by the system, not directly user-facing

These kinds are mostly for UI, filtering, and prompting. They should not drive
complex branching behavior on their own.

---

## Lifecycle Model

The existing statuses are already close to what we need:

- `ACTIVE`
- `AWAITING_INPUT`
- `AWAITING_CONFIRMATION`
- `COMPLETED`
- `FAILED`
- `CANCELLED`

Keep those for V1.

Interpret them as:

- `ACTIVE`: the agent can continue immediately on the next relevant run
- `AWAITING_INPUT`: blocked on a user reply or explicit user decision
- `AWAITING_CONFIRMATION`: blocked on confirmation for a risky next action
- `COMPLETED`: goal achieved
- `FAILED`: task cannot continue without manual restart or replan
- `CANCELLED`: user or system intentionally stopped it

Suggested future addition, but not required for V1:

- `PAUSED`: intentionally dormant but not waiting on a concrete input

---

## Proposed Data Model

### Recommendation

Do **not** replace the current `Task` model immediately. Extend it in a way that
keeps reminders/actions working.

Recommended V1 shape:

#### Keep `Task` as the task header row

Add a few structured fields:

```text
task
├── id
├── household_id
├── user_id
├── title
├── task_kind             "plan" | "track" | "prepare" | ...
├── status
├── current_step
├── context_json
├── summary               short human-readable progress summary
├── awaiting_input_hint   short question / what the user must answer
├── resume_after          nullable datetime
├── last_agent_run_id     nullable agent run link
├── trigger_event_id
├── created_at
├── updated_at
└── completed_at
```

Notes:

- `summary` should be the compact text injected into context
- `awaiting_input_hint` gives the runtime and UI a crisp blocking reason
- `resume_after` supports time-based wakeups without making the scheduler the source of truth

#### Add `TaskStep`

Move step tracking out of the `steps` JSON blob into a normalized table.

```text
task_step
├── id
├── task_id
├── step_index
├── title
├── status               "pending" | "active" | "done" | "failed" | "cancelled"
├── step_type            "research" | "decision" | "tool" | "wait" | "message"
├── details_json
├── started_at
├── completed_at
└── updated_at
```

Why:

- easier partial updates
- easier admin inspection
- easier future metrics
- avoids fragile JSON patching

#### Add `TaskLink`

Store canonical references from a task to world-model entities or other objects.

```text
task_link
├── id
├── task_id
├── entity_type          "member" | "calendar" | "place" | "device" | "routine" | "scheduled_prompt"
├── entity_id
├── role                 "subject" | "source" | "target" | "selected_option"
└── created_at
```

This is the clean bridge to the world model.

#### Keep `context_json`, but narrow its purpose

`context_json` should hold bounded working state only, for example:

```json
{
  "selected_member_id": "wm-member-123",
  "candidate_calendar_ids": ["cal-1", "cal-2"],
  "options": [
    {"label": "Option A", "source": "calendar"},
    {"label": "Option B", "source": "manual"}
  ],
  "decision_required": true
}
```

Do not let it become an unbounded transcript dump.

---

## Why Not Just Keep `steps` As JSON

You can ship a tiny V1 with JSON steps, but it becomes painful quickly:

- hard to inspect in admin UI
- hard to patch atomically
- easy for model-written state to drift
- poor queryability

A normalized `TaskStep` table is the lowest-cost upgrade that pays off quickly.

---

## Runtime Components

Introduce a dedicated task orchestration layer:

- `app/tasks/service.py`
- `app/tasks/repository.py`
- `app/tasks/resolution.py`

Responsibilities:

### `TaskRepository`

- CRUD on `Task`, `TaskStep`, `TaskLink`
- scoped lookups for active tasks by user/household
- optimistic update helpers

### `TaskService`

- create task from model request
- resume task
- advance step
- mark awaiting input
- mark awaiting confirmation
- complete / fail / cancel
- schedule wakeups or follow-on actions when needed

### `TaskResolution`

Determines which task an incoming user message should attach to.

Rules:

1. If the user explicitly references a task ID/title, use that task.
2. Else if exactly one open task is clearly relevant, attach to it.
3. Else if multiple are open, ask a clarifying question or show a short list.
4. Else treat it as a fresh request.

This prevents "continue" from resuming the wrong thing.

---

## Context Injection Design

### Current gap

Task state is not currently injected into normal agent runs.

### Proposed injection

When a relevant open task exists, add a compact `## Active Task` section:

```text
## Active Task
- title: Plan Sondre football logistics for next week
- status: awaiting_input
- summary: Calendar options gathered. Two transport conflicts identified.
- current step: choose how to handle Thursday overlap
- waiting for: user decision on Thursday pickup
- linked entities:
  - member: Sondre
  - calendar: Football
```

If multiple tasks exist, include only:

- the explicitly selected one, or
- the top one chosen by the resolution layer

Do not dump every active task into every run.

---

## Agent Tools

The agent needs explicit task tools. Suggested V1 set:

### `create_task(...)`

Creates a new multi-step task.

Inputs:

- `title`
- `task_kind`
- `summary`
- `steps`
- `linked_entities`

### `update_task_progress(...)`

Updates summary, current step, step states, and compact task context.

Inputs:

- `task_id`
- `summary`
- `current_step`
- `step_updates`
- `context_patch`

### `await_task_input(...)`

Marks the task blocked on user input.

Inputs:

- `task_id`
- `prompt_hint`
- `current_step`

### `complete_task(...)`

Marks done with optional result summary.

### `fail_task(...)`

Marks failed with a short reason.

### `cancel_task(...)`

Explicit cancellation.

### `list_tasks(...)`

Useful for user-facing inspection and admin/debugging.

### `link_task_entity(...)`

Optional helper if entity linking should happen incrementally rather than only at create-time.

---

## State Transition Rules

The runtime should enforce simple allowed transitions.

Recommended V1:

- `ACTIVE -> AWAITING_INPUT`
- `ACTIVE -> AWAITING_CONFIRMATION`
- `ACTIVE -> COMPLETED`
- `ACTIVE -> FAILED`
- `ACTIVE -> CANCELLED`
- `AWAITING_INPUT -> ACTIVE`
- `AWAITING_INPUT -> CANCELLED`
- `AWAITING_CONFIRMATION -> ACTIVE`
- `AWAITING_CONFIRMATION -> CANCELLED`

Disallow arbitrary model-driven transitions like:

- `COMPLETED -> ACTIVE`
- `FAILED -> ACTIVE`

If reactivation is needed later, create a new task or add an explicit admin-only restart path.

---

## Suggested Execution Flow

### 1. New incoming message

```text
message arrives
-> resolve user
-> resolve whether this message belongs to an existing task
-> assemble normal context
-> inject selected task summary if present
-> run agent
-> persist task mutations proposed via task tools
-> persist conversation + memory as usual
```

### 2. When the agent realizes work spans multiple turns

Example:

- user: "Help me plan dinner for Saturday"
- agent researches options
- agent creates a task with steps:
  - gather candidates
  - compare
  - wait for user choice
  - finalize
- agent marks `AWAITING_INPUT` after presenting choices

### 3. When the user replies later

Example:

- user: "Let's do option 2"
- resolution layer attaches reply to the dinner-planning task
- task section is injected
- agent resumes from the last step instead of inferring from fuzzy conversation alone

### 4. When the task involves a timed follow-up

Example:

- task stays active
- runtime schedules a reminder or scheduled prompt as a sub-action
- task `context_json` stores the linked scheduled object ID
- when the scheduled callback fires, it updates or completes the parent task

---

## Relationship To Existing Reminder / Action Tasks

Do not break the current uses of `Task`.

Recommendation:

- keep reminders/actions as valid `Task` rows
- classify them with `task_kind = "handoff"` or `task_kind = "scheduled_action"`
- continue letting scheduler jobs mark them completed/failed

This preserves backward compatibility while allowing general tasks to use the same top-level table.

Longer term, you can decide whether reminders/actions stay in `Task` or get their
own tables plus a parent task link. V1 should not force that migration.

---

## Interaction With Policy Gate

`AWAITING_CONFIRMATION` should not be a separate ad-hoc concept disconnected from
the existing policy gate.

Recommended behavior:

1. agent decides the next step requires a risky side effect
2. agent may create/update the task first
3. task moves to `AWAITING_CONFIRMATION`
4. policy gate creates `PendingAction`
5. confirmation callback executes action
6. callback updates task back to `ACTIVE` or `COMPLETED`

This prevents the common bug where the side effect is confirmed, but the task
state still thinks it is blocked.

---

## Interaction With The World Model

This is where the new world model matters.

Tasks should link to canonical entities whenever possible:

- members
- calendars
- devices
- places
- routines

Benefits:

- better resumption
- better disambiguation
- cleaner admin UI
- easier future event-driven triggers

Example:

Instead of:

```json
{"person": "Sondre", "calendar": "football"}
```

Prefer:

```json
{
  "member_id": "member-uuid",
  "calendar_id": "calendar-uuid"
}
```

with optional display labels cached separately for convenience.

---

## Interaction With Memory

Task state should **not** be treated as memory.

Use each layer for what it is good at:

- task layer: active operational progress
- world model: durable household structure
- episodic memory: softer durable recall
- conversation history: recent exchange continuity

When a task completes, you may derive memories from the conversation or outcome,
but do not retain the whole finished task as prompt baggage forever.

---

## Admin / UX Implications

The admin UI should gain a task view with:

- open tasks by user
- status
- current step
- summary
- linked entities
- last updated time

Useful admin actions:

- inspect task
- cancel task
- mark failed
- resume task
- trigger run-now for a paused/waiting task

For user-facing chat UX, add:

- "What are you working on?"
- "Cancel that"
- "Continue"
- optional future slash command: `/tasks`

---

## Observability

Add task-specific events to the existing event stream:

- `task.create`
- `task.update`
- `task.await_input`
- `task.await_confirmation`
- `task.complete`
- `task.fail`
- `task.cancel`

These should include:

- `task_id`
- `user_id`
- `status`
- `title`
- `current_step`

This makes task debugging much easier than reading free-form model outputs.

---

## Failure Modes And Mitigations

### 1. The model creates too many unnecessary tasks

Mitigation:

- strong prompt guidance on when to create one
- runtime heuristics for minimum complexity
- one-open-task-per-user soft limit in V1 unless explicitly needed

### 2. A reply attaches to the wrong task

Mitigation:

- deterministic task resolution layer
- explicit disambiguation when more than one active candidate exists

### 3. Task context grows into a junk drawer

Mitigation:

- keep `summary` short
- keep `context_json` bounded
- normalize steps and links into tables

### 4. Side effects happen twice

Mitigation:

- keep using conversation turn history
- keep using policy gate for risky actions
- add task-level idempotency keys for scheduled follow-ups when needed

### 5. Task never exits waiting state

Mitigation:

- `awaiting_input_hint`
- optional stale-task sweeper later
- admin visibility

---

## Recommended Implementation Plan

### Phase 1: Resumable conversational tasks

Deliver:

- `TaskService` + `TaskRepository`
- task resolution on incoming messages
- compact `## Active Task` context injection
- agent task tools: create/update/await/complete/cancel/list
- one task view in admin

Keep scope tight:

- one primary open task per user in normal chat flow
- no task graph
- no automatic background continuation except existing scheduler primitives

This is the first useful milestone.

### Phase 2: Normalize and link

Deliver:

- `TaskStep`
- `TaskLink`
- world-model ID linkage
- cleaner UI / inspection

This makes the system durable instead of prompt-fragile.

### Phase 3: Timed and event-driven continuation

Deliver:

- `resume_after`
- parent/child task handoffs to reminders/scheduled prompts/actions
- scheduled callbacks that update parent tasks

### Phase 4: Smarter routing and stale-task hygiene

Deliver:

- better task selection when multiple are open
- stale-task reminders
- admin recovery tools

---

## Concrete V1 Recommendation

If choosing the most pragmatic path for this repo right now:

1. Keep the current `Task` table.
2. Add `task_kind`, `summary`, `awaiting_input_hint`, `resume_after`, and `last_agent_run_id`.
3. Add a normalized `TaskStep` table.
4. Add a `TaskService` and inject one selected task into context.
5. Add explicit task agent tools.
6. Leave reminders/actions on `Task` for compatibility.
7. Do not attempt event-driven autonomy in the first iteration.

That is enough to move from "task-shaped rows exist" to "the agent can reliably
pause, resume, and complete multi-turn work".

---

## Success Criteria

The design is successful when all of these are true:

- the agent can pause after partial progress and resume correctly on a later reply
- the current step is explicit and inspectable
- user replies like "continue" or "option 2" resume the intended task reliably
- reminders and scheduled actions still work without regression
- task state is smaller and more stable than relying on conversation text alone
- tasks are grounded in world-model entities where appropriate
- admins can inspect and recover stuck tasks without editing raw DB JSON
