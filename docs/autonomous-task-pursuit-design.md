# Autonomous Task Pursuit Design

## Purpose

This document proposes the next layer above HomeAgent's current multi-step task
support: durable autonomous task pursuit.

The current task system can remember a task, show the agent the active task
state, update progress, and schedule a future resume. That is enough for
resumable work, but not enough for the stronger behavior we want:

- keep trying until the task is solved, blocked, or safely failed
- try different approaches when one approach does not work
- remember what was tried and why it did or did not work
- wake up later with the exact reason and expected observation
- expose enough runtime events for the admin dashboard to show what is happening

The goal is not to build a general workflow engine. The goal is to give the
existing single-agent runtime enough durable state and explicit operations to
pursue a task across multiple runs without relying on conversational memory.

---

## Current State

The useful scaffolding already exists:

- `assemble_context()` injects active task context into every agent run.
- `Task`, `TaskStep`, and `TaskLink` persist task state.
- `update_task_progress(...)` lets the agent update summaries, step statuses,
  and task context.
- `schedule_task_resume(...)` lets the agent schedule a timed follow-up.
- `resume_task()` wakes the agent through the normal `agent_run()` path.
- Admin APIs and the SSE stream already carry `task.*`, `run.*`, `job.*`, and
  `control.*` events.

The main gap is not missing tables. The gap is that the task state machine does
not yet model autonomous pursuit explicitly.

Today the agent can say "task updated". It cannot reliably say:

- "I tried approach A, got result X, so next I will try B."
- "This step is done; store this checkpoint and activate the next step."
- "Wake me in 10 minutes specifically to verify this expected state."
- "I have retried enough; this is blocked on the user or failed safely."

---

## Design Goals

1. Make autonomous progress durable across runs.
2. Give the agent explicit tools for attempts, step advancement, replanning,
   timed follow-up, completion, and failure.
3. Preserve runtime ownership of status transitions, retries, idempotency, and
   safety gates.
4. Make every meaningful autonomous decision visible in the admin event stream.
5. Bound autonomous behavior with budgets, retry limits, and escalation rules.
6. Keep the state compact enough to inject into prompts without dumping logs.
7. Reuse the current task model where possible.

---

## Non-Goals

- No multi-agent planner/executor split.
- No arbitrary workflow DSL.
- No unbounded background loops.
- No hidden unsafe side effects.
- No persistent event replay requirement for V1.
- No requirement that every normal conversational task becomes autonomous.

---

## Core Concept

An autonomous task needs two layers of state:

1. **Task state**: what goal is being pursued and where the task is in the
   lifecycle.
2. **Pursuit state**: what the agent has tried, what it learned, what it will
   try next, and when it should stop.

The task remains the user-visible durable work item. The pursuit state is a
compact operational record attached to that task.

Suggested task context shape:

```json
{
  "pursuit": {
    "objective": "Verify that the hallway motion automation is working",
    "mode": "autonomous",
    "current_approach": "Check latest device state and recent events",
    "next_action": "Wait for the next motion event and verify light state",
    "attempt_count": 2,
    "max_attempts": 5,
    "retry_policy": {
      "base_delay_seconds": 300,
      "max_delay_seconds": 3600,
      "backoff": "linear"
    },
    "escalation_condition": "No relevant event after 3 follow-ups or tool access fails twice",
    "last_attempt": {
      "attempt_id": "uuid",
      "result": "partial",
      "summary": "Device state read succeeded, but no fresh event was observed"
    },
    "resume": {
      "reason": "Check whether a fresh motion event arrived",
      "expected_observation": "A new motion event for hallway sensor",
      "resume_at": "2026-04-24T12:30:00+02:00"
    }
  }
}
```

This should be rendered into the active task prompt as a compact section, not as
raw JSON.

---

## Lifecycle Model

Keep the existing high-level statuses:

- `ACTIVE`
- `AWAITING_INPUT`
- `AWAITING_CONFIRMATION`
- `COMPLETED`
- `FAILED`
- `CANCELLED`

Add one status:

- `AWAITING_RESUME`

Interpretation:

- `ACTIVE`: the agent may continue immediately if invoked.
- `AWAITING_RESUME`: the task is waiting for a scheduled autonomous follow-up.
- `AWAITING_INPUT`: the task is blocked on a user answer.
- `AWAITING_CONFIRMATION`: the task is blocked on a risky action confirmation.
- `COMPLETED`: the goal was achieved.
- `FAILED`: the task cannot continue under its current constraints.
- `CANCELLED`: the user or admin intentionally stopped it.

Why add `AWAITING_RESUME`:

- time-based waits are not user-input waits
- admin views can distinguish "waiting for user" from "scheduled autonomous
  retry"
- stale-task cleanup can treat the two states differently
- resume jobs can validate that the task is still waiting for the expected timer

Allowed transitions:

```text
ACTIVE
  -> AWAITING_INPUT
  -> AWAITING_CONFIRMATION
  -> AWAITING_RESUME
  -> COMPLETED
  -> FAILED
  -> CANCELLED

AWAITING_RESUME
  -> ACTIVE
  -> FAILED
  -> CANCELLED

AWAITING_INPUT
  -> ACTIVE
  -> CANCELLED

AWAITING_CONFIRMATION
  -> ACTIVE
  -> CANCELLED
```

Terminal states should remain terminal.

---

## Data Model

### V1: Reuse `Task.context` and `TaskStep.details_json`

For the first implementation, do not add a new table unless the JSON becomes
too large or hard to query.

Use:

- `Task.context["pursuit"]` for compact current pursuit state.
- `TaskStep.details_json` for step-specific checkpoint notes.
- `Task.summary` for the short human-readable current state.
- `Task.resume_after` for the next scheduled autonomous wakeup.

Recommended addition:

- `Task.status = "AWAITING_RESUME"`

Optional later addition:

```text
TaskAttempt
├── id
├── task_id
├── attempt_index
├── approach
├── actions_taken_json
├── result              "success" | "partial" | "failed" | "blocked"
├── result_note
├── next_action
├── run_id
├── started_at
└── completed_at
```

Use a `TaskAttempt` table later if:

- the admin UI needs detailed attempt history
- prompt context starts bloating
- analytics over retries/failures becomes important
- multiple attempts need to be inspected after task completion

### Context Size Rule

Keep only the current pursuit state and the last few attempt summaries in
`Task.context`.

Suggested shape:

```json
{
  "pursuit": {
    "attempt_count": 3,
    "max_attempts": 5,
    "current_approach": "...",
    "next_action": "...",
    "last_attempt": {...},
    "recent_attempts": [
      {"result": "failed", "summary": "Tool call timed out"},
      {"result": "partial", "summary": "State read succeeded; no event yet"}
    ]
  }
}
```

Cap `recent_attempts` to 3-5 entries.

---

## Agent Tools

The agent needs narrow tools with clear semantics. Keep the generic
`update_task_progress(...)`, but add explicit tools for autonomous pursuit.

### `record_task_attempt(...)`

Append or merge a structured attempt summary into task pursuit state.

Inputs:

- `task_id`
- `approach`
- `actions_taken`
- `result`: `success | partial | failed | blocked`
- `result_note`
- `next_action`
- `retryable`: boolean

Runtime behavior:

- increments `pursuit.attempt_count`
- updates `current_approach`, `last_attempt`, `next_action`
- appends to capped `recent_attempts`
- updates `Task.summary`
- emits `task.attempt_recorded`

This is the most important new operation. It is what lets the agent try
different approaches without losing track of what already happened.

### `advance_task_step(...)`

Complete or fail one step and optionally activate the next step.

Inputs:

- `task_id`
- `step_index`
- `status`: `done | failed | cancelled`
- `result_note`
- `activate_next`: boolean, default `true`

Runtime behavior:

- validates the target step belongs to the task
- writes `result_note` into `TaskStep.details_json`
- sets timestamps
- if `status=done` and `activate_next=true`, activates the next pending step
- updates `Task.current_step`
- emits `task.step_advanced` or `task.step_failed`

This should use or refine the existing repository-level `advance_step(...)`
helper.

### `replan_task(...)`

Replace or append remaining steps when the current plan is not working.

Inputs:

- `task_id`
- `reason`
- `new_steps`
- `preserve_completed`: boolean, default `true`

Runtime behavior:

- keeps completed steps intact by default
- cancels or supersedes unfinished steps
- creates new pending steps
- activates the first new step if no step is currently active
- stores `pursuit.replan_reason`
- emits `task.replanned`

Use this when the agent has learned that the original approach is wrong, not
for minor progress updates.

### `schedule_task_followup(...)`

Schedule an autonomous resume with intent.

Inputs:

- `task_id`
- `resume_at_iso` or `delay_seconds`
- `reason`
- `expected_observation`
- `retry_policy_override` optional

Runtime behavior:

- transitions `ACTIVE -> AWAITING_RESUME`
- stores `Task.resume_after`
- stores `pursuit.resume.reason`
- stores `pursuit.resume.expected_observation`
- schedules APScheduler `resume_task`
- emits `task.followup_scheduled`

This should replace or wrap the current `schedule_task_resume(...)` tool. The
existing tool can remain as a compatibility alias.

### `complete_task(...)`

Already exists. Extend semantics:

- require or strongly encourage an `outcome` summary
- clear `resume_after`
- clear `pursuit.resume`
- emit `task.complete`

### `fail_task(...)`

Add an explicit failure tool.

Inputs:

- `task_id`
- `reason`
- `recoverable`: boolean
- `suggested_user_action` optional

Runtime behavior:

- transitions task to `FAILED`
- stores failure reason in `Task.summary` and `Task.context["pursuit"]`
- clears scheduled resume metadata
- emits `task.fail`

Use this when the task cannot continue safely or usefully without a manual
change.

### `await_task_input(...)`

Already exists. Keep it for user-blocked tasks only.

Runtime behavior should explicitly clear autonomous resume state.

---

## Resume Semantics

The resume prompt must include why the timer exists.

Current generic prompt:

```text
[Task resume] The scheduled follow-up time has arrived for task ...
Please review the task state and continue or report back to the user.
```

Recommended prompt:

```text
[Task resume]
Task ID: ...
Reason: Check whether the hallway motion event arrived.
Expected observation: A new motion event for hallway sensor.
Previous next action: If no event arrived, try reading Homey state directly.

Review the task state, record an attempt, then either continue, schedule another
follow-up, ask the user, complete the task, or fail it safely.
```

`resume_task()` should:

1. load the task
2. skip terminal tasks
3. verify status is `AWAITING_RESUME` or otherwise resumable
4. read `pursuit.resume`
5. transition to `ACTIVE`
6. clear `resume_after`
7. run `agent_run()` with the rich resume prompt
8. emit `task.resume_started`
9. emit `task.resume_completed` or `task.resume_failed`

The resume reason should not be lost before prompt construction.

---

## Retry Policy

Autonomous pursuit must be bounded.

Each autonomous task should have:

- `attempt_count`
- `max_attempts`
- `retry_policy`
- `escalation_condition`

Default retry policy:

```json
{
  "max_attempts": 5,
  "base_delay_seconds": 300,
  "max_delay_seconds": 3600,
  "backoff": "linear"
}
```

Recommended behavior:

- The runtime increments attempt count in `record_task_attempt(...)`, not the
  model.
- If `attempt_count >= max_attempts`, the next follow-up scheduling request
  should be rejected unless the task is explicitly replanned.
- If a result is `blocked`, the agent should move to `AWAITING_INPUT` or
  `FAILED`, not schedule infinite retries.
- Tool transport errors may be retryable.
- Policy/permission blocks are not retryable unless the user changes the
  constraints.
- Repeated identical failures should force replan or fail.

Delay calculation:

```text
linear:      min(base_delay_seconds * attempt_count, max_delay_seconds)
exponential: min(base_delay_seconds * 2^(attempt_count - 1), max_delay_seconds)
fixed:       base_delay_seconds
```

For V1, let the agent choose the next delay but let the runtime enforce:

- no resume in the past
- no delay below a minimum floor for autonomous retries
- no delay above a maximum unless explicitly requested by the user
- no scheduling after retry budget is exhausted

Suggested defaults:

- minimum autonomous retry delay: 60 seconds
- maximum autonomous retry delay: 24 hours
- maximum attempts without user involvement: 5

---

## Failure Logic

Failure should be explicit and inspectable.

Use `FAILED` when:

- retry budget is exhausted
- a required tool or integration is unavailable across repeated attempts
- the agent lacks permission to continue
- the task objective is impossible under current constraints
- the expected external condition never occurs and waiting longer is not useful
- repeated verification fails after an action

Use `AWAITING_INPUT` when:

- the user must choose between approaches
- the user must provide missing information
- continuing autonomously would be ambiguous or risky

Use `AWAITING_CONFIRMATION` when:

- the next step is a side effect requiring confirmation
- the policy gate requires confirmation

Use `CANCELLED` when:

- the user or admin intentionally stops the task
- the original goal is no longer desired

Use `COMPLETED` only when:

- the objective is achieved
- or the user explicitly accepts a partial outcome as sufficient

The agent should not silently abandon an autonomous task. Every stop condition
must become `COMPLETED`, `FAILED`, `CANCELLED`, `AWAITING_INPUT`, or
`AWAITING_CONFIRMATION`.

---

## Resilience

### Process Restarts

APScheduler jobs may be restored on startup for tasks with `resume_after`.

Startup restore should include `AWAITING_RESUME` tasks whose `resume_after` is
in the future.

For overdue autonomous resumes:

- if overdue by a small amount, run soon
- if overdue beyond a configurable stale threshold, emit `task.resume_missed`
  and either schedule a near-term retry or fail the task depending on policy

### Duplicate Scheduling

Use deterministic schedule IDs:

```text
task:{task_id}:resume
```

Scheduling a new follow-up should replace the previous pending follow-up for
the same task unless the task explicitly supports multiple timers.

### Idempotency

Side-effecting tools remain protected by the existing policy gate and
confirmation flow.

For autonomous follow-ups:

- include `run_id` in attempt records
- record expected observation before scheduling
- record observed result after resume
- do not repeat a side effect just because a resume fired twice

### Locking

Keep using `get_user_run_lock(user_id)` for resume-triggered `agent_run()`.

The lock prevents:

- a user reply racing a scheduled resume
- two task resumes mutating the same user's context simultaneously
- an event-triggered run and timer-triggered run interleaving incorrectly

### Corrupted Context

If `Task.context` cannot be parsed:

- emit `task.context_error`
- preserve the raw value if possible
- continue with an empty pursuit state only if the task can be safely handled
- otherwise move to `FAILED` with a clear reason

### Tool Failures

The agent should distinguish:

- transient tool failure: retry or schedule follow-up
- deterministic tool failure: replan or fail
- policy failure: ask user or await confirmation
- missing integration: fail or ask admin/user, depending on task kind

Runtime should emit both the normal tool/run error events and a task-level
attempt event so the admin UI can correlate the failure to the durable task.

---

## Event Stream Requirements

The admin dashboard should not infer autonomous progress only from free-form
assistant messages. Emit task-level events for all important state changes.

Recommended new events:

| Event | When |
|-------|------|
| `task.attempt_recorded` | An autonomous attempt is recorded |
| `task.step_advanced` | A step is completed and the next step may be activated |
| `task.step_failed` | A step fails |
| `task.replanned` | Remaining steps are rewritten or appended |
| `task.followup_scheduled` | A timed autonomous resume is scheduled |
| `task.resume_started` | A scheduled task resume begins |
| `task.resume_completed` | A scheduled resume run completes |
| `task.resume_failed` | A scheduled resume run fails |
| `task.resume_missed` | A stored resume was overdue or not restored cleanly |
| `task.retry_budget_exhausted` | Retry budget prevents another autonomous retry |
| `task.context_error` | Task context is malformed or unusable |
| `task.fail` | Task is explicitly failed |

Event payloads should include enough fields for the admin stream and details
view without another DB lookup.

Example `task.attempt_recorded`:

```json
{
  "task_id": "uuid",
  "attempt_count": 3,
  "max_attempts": 5,
  "approach": "Read Homey device state directly",
  "result": "partial",
  "retryable": true,
  "next_action": "Wait for another motion event",
  "run_id": "uuid"
}
```

Example `task.followup_scheduled`:

```json
{
  "task_id": "uuid",
  "resume_at": "2026-04-24T12:30:00+02:00",
  "reason": "Check whether a fresh motion event arrived",
  "expected_observation": "Motion event from hallway sensor",
  "attempt_count": 2,
  "max_attempts": 5
}
```

Example `task.retry_budget_exhausted`:

```json
{
  "task_id": "uuid",
  "attempt_count": 5,
  "max_attempts": 5,
  "last_result": "failed",
  "next_required_action": "Ask user or fail task"
}
```

Existing events to keep:

- `task.create`
- `task.update`
- `task.await_input`
- `task.complete`
- `task.cancel`
- `task.schedule_resume` as compatibility alias if retained
- `run.start`
- `run.complete`
- `run.error`
- `job.fire`
- `job.complete`
- `job.error`
- `control.task_created`
- `control.task_reused`

---

## Admin UI Implications

The task detail view should show:

- objective
- current approach
- current step
- attempt count / max attempts
- last attempt result
- next action
- retry/follow-up schedule
- expected observation
- failure or escalation condition

The control-loop tab should use these fields to answer:

- What is the agent trying to solve?
- What did it just try?
- What will it try next?
- Is it waiting for time, user input, confirmation, or an external event?
- How many attempts remain?
- Why did it stop?

For stream rendering, add badges for:

- attempt
- replan
- follow-up
- resume
- retry exhausted
- failed

The admin API should expose parsed `context.pursuit` in `/admin/tasks`, similar
to how it already exposes parsed `context.control`.

Suggested response addition:

```json
{
  "pursuit": {
    "objective": "...",
    "mode": "autonomous",
    "current_approach": "...",
    "next_action": "...",
    "attempt_count": 2,
    "max_attempts": 5,
    "last_attempt": {...},
    "resume": {...},
    "escalation_condition": "..."
  }
}
```

---

## Prompt Context Rendering

Render pursuit state into `## Active Task`.

Suggested format:

```text
## Active Task
- title: Verify hallway automation
- kind: track
- status: AWAITING_RESUME
- summary: Waiting to verify whether a new motion event turns on the hallway light.
- objective: Verify hallway automation works end-to-end.
- current approach: Wait for a fresh motion event, then check light state.
- attempts: 2 / 5
- last attempt: partial — Device state read succeeded, but no fresh event was observed.
- next action: Check event stream after scheduled resume.
- scheduled resume: 2026-04-24T12:30:00+02:00
- resume reason: Check whether a fresh motion event arrived.
- expected observation: Motion event from hallway sensor.
- steps:
  [x] Check current Homey state
      result: Hallway sensor online; no fresh event observed.
  [>] Wait for fresh motion event
- task ID: ...
```

Rules:

- include only compact recent attempt summaries
- do not dump raw tool output unless it is short and relevant
- include retry budget when autonomous mode is active
- include resume reason and expected observation when waiting

---

## Safety Boundaries

Autonomous pursuit does not bypass the existing policy gate.

Rules:

- risky side effects still require confirmation
- scheduled autonomous resumes may observe, plan, verify, and report
- scheduled autonomous resumes must not execute confirmation-required actions
  unattended
- if a next action requires confirmation, transition to
  `AWAITING_CONFIRMATION`
- if a next action is ambiguous or materially changes user intent, transition to
  `AWAITING_INPUT`

The runtime should prefer safe failure over repeated risky retries.

---

## Implementation Plan

### Phase 1: Durable Attempt And Resume Intent

- Add `AWAITING_RESUME` to task status transitions.
- Add `record_task_attempt(...)`.
- Add `schedule_task_followup(...)` with reason and expected observation.
- Update `resume_task()` to build a rich resume prompt from pursuit state.
- Emit `task.attempt_recorded`, `task.followup_scheduled`,
  `task.resume_started`, and `task.resume_completed`.
- Render pursuit state in `_render_full_task()`.

### Phase 2: Step Control And Failure

- Add `advance_task_step(...)`.
- Add `fail_task(...)`.
- Render `TaskStep.details_json` result notes into context and admin detail.
- Emit `task.step_advanced`, `task.step_failed`, and `task.fail`.

### Phase 3: Replanning And Retry Enforcement

- Add `replan_task(...)`.
- Enforce retry budget in `schedule_task_followup(...)`.
- Emit `task.replanned` and `task.retry_budget_exhausted`.
- Add stale/overdue resume recovery on scheduler startup.

### Phase 4: Admin Visibility

- Expose parsed `context.pursuit` in `/admin/tasks`.
- Add pursuit fields to task detail UI.
- Add stream badges/rendering for new `task.*` events.
- Add filters for waiting-on-user, waiting-on-resume, retry-exhausted, and
  failed autonomous tasks.

---

## Open Questions

1. Should V1 store attempts only in `Task.context`, or should it add a
   `TaskAttempt` table immediately?
2. Should `AWAITING_RESUME` be added now, or should V1 keep `ACTIVE +
   resume_after` to avoid a migration?
3. Should retry policy be global config, per task, or both?
4. Should admin be able to edit retry budget and next action directly?
5. Should event-triggered control tasks use the same pursuit fields, or should
   `context.control` remain separate and only link to pursuit state?

Recommended answers:

- Use `Task.context` first; add `TaskAttempt` later if needed.
- Add `AWAITING_RESUME`; the semantic clarity is worth the small migration.
- Use global defaults with per-task overrides.
- Let admin cancel/resume first; editing retry policy can come later.
- Keep `control` and `pursuit` separate but render them together in admin.

