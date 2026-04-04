# Control Loop Admin Tab Design

## Purpose

This document proposes a new **Control Loop** tab in the admin UI.

The goal is to provide a clear, near-real-time operational view of what the
autonomy/control-loop system is doing now, without introducing a second
observability stack or a large amount of new instrumentation.

The tab should help answer questions like:

- What control-loop work is active right now?
- Which tasks are ongoing, blocked, or waiting?
- Which events recently triggered autonomous runs?
- What goals has the system identified?
- What reminders, scheduled prompts, or task resumes are pending?
- What memory and world-model side effects are happening as part of the loop?

This should be done **primarily by reusing existing event emissions, admin
endpoints, and stored task/scheduler data**.

---

## Design Goals

1. Give a simple operational picture of the control loop.
2. Reuse current SSE event stream and existing admin endpoints wherever possible.
3. Show durable state from tasks and scheduler, not only transient feed items.
4. Stay lightweight in both implementation and runtime cost.
5. Avoid adding a second logging or tracing system.
6. Make phase 2 / phase 3 control-loop behavior easier to debug and discuss.

---

## Non-Goals

- No full replay/debugger for the control loop
- No new high-volume event persistence layer
- No heavy polling dashboard
- No exact distributed-tracing style timeline
- No replacement of the existing Live / Tasks / Scheduler tabs

This tab is an operational overview, not a forensic tool.
It should give a glimpse of what is happening, being used for demos etc.

---

## Current Reusable Foundations

The codebase already provides most of what this tab needs.

### Existing real-time event stream

Current SSE events already include or can include:

- `run.start`
- `run.complete`
- `run.error`
- `run.background_error`
- `job.fire`
- `job.complete`
- `job.error`
- `task.create`
- `task.update`
- `task.await_input`
- `task.complete`
- `task.cancel`
- `task.link`
- `task.schedule_resume`
- `event.received`
- `event.synced`
- `event.triggered`
- `event.suppressed`
- `control.task_created`
- `control.task_reused`
- `mem.extract`
- `mem.summarize`
- `world.update`
- `world.proposal`
- `proactive.fire`
- `proactive.deliver`
- `proactive.skip`
- `proactive.fail`

These are already enough to drive a meaningful near-real-time view.

### Existing admin endpoints

The following existing endpoints are especially useful:

- `/admin/stats`
- `/admin/tasks`
- `/admin/scheduler`
- `/admin/event-rules`
- `/admin/stream`

### Existing durable models

The durable state already exists in:

- `Task`, `TaskStep`, `TaskLink`
- `ScheduledPrompt`, `ScheduledPromptRun`, `ScheduledPromptLink`
- `AgentRunLog`
- `DeviceSnapshot`
- `EventRule`

The new tab should rely on these, not re-invent them.

---

## User Experience

The new tab should feel like:

- a concise real-time operations board
- focused on control-loop state rather than raw logs
- easy to glance at during testing or debugging

The tab should answer, at a glance:

- Is the control loop healthy?
- What is active?
- What is waiting?
- What just happened?

---

## Proposed Tab Structure

## 1. Top Status Strip

A compact strip of status cards.

Suggested fields:

- event dispatcher: enabled / disabled
- SSE: connected / disconnected
- event bus size
- active event rules
- active control tasks
- active user-visible tasks
- pending reminders
- pending scheduled actions
- enabled scheduled prompts

Purpose:

- show whether the control plane is alive
- show whether there is queued or active work

Data sources:

- `/admin/stats`
- `/admin/scheduler`
- `/admin/tasks`
- `/admin/event-rules`

Minimal backend additions likely needed:

- `event_dispatcher_enabled`
- `event_bus_size`
- `event_rules_total`
- `control_tasks_active`

---

## 2. Active Control Work

This is the main section of the tab.

Show a list of active control-loop items as cards or compact rows.

Each item should represent one durable ongoing work item, usually a task.

Suggested fields per item:

- title
- task ID
- task kind
- task status
- control phase, if present
- user
- summary
- last updated time
- waiting reason
- linked entities
- last triggering event summary
- verification pending flag
- correlation key (collapsed / secondary)

How to identify control items:

- task context contains `control`
- or task links include an `event_rule` link
- or task kind is `track`/`admin` and the context is clearly loop-related

Primary data source:

- `/admin/tasks`

Minimal backend addition needed:

- `/admin/tasks` should expose parsed `context.control` when present

Without that, the tab can still show tasks, but not the most valuable control
metadata.

---

## 3. Waiting / Blocked

A dedicated panel for things that are paused or waiting on something else.

Suggested categories:

- tasks awaiting user input
- tasks awaiting confirmation
- tasks with `resume_after`
- scheduled prompts waiting to fire
- reminders waiting to fire
- scheduled actions waiting to fire

This helps answer:

- what work is pending but inactive?

Data sources:

- `/admin/tasks`
- `/admin/scheduler`

This should mostly be a filtered view of data already available elsewhere.

---

## 4. Recent Control Activity

A real-time timeline derived from the SSE stream, but filtered to loop-relevant
events only.

Suggested event families to include:

- `event.*`
- `control.*`
- `task.*`
- `run.start`
- `run.complete`
- `run.error`
- `job.*`
- `proactive.*`
- `mem.extract`
- `mem.summarize`
- `world.update`
- `world.proposal`
- `run.background_error`

Purpose:

- show the latest movement in the loop
- give recency without forcing the user into raw logs

This should **not** be a duplicate of the existing Live tab.

Difference from Live tab:

- smaller scope
- filtered for loop-relevant signals
- grouped/worded for loop comprehension, not generic runtime activity

---

## 5. Current In-Flight Runs

Show runs currently in progress, derived client-side from the SSE stream.

Suggested behavior:

- add an item on `run.start`
- remove it on `run.complete` or `run.error`
- enrich with trigger, model, user, rough context size, and current elapsed time

Purpose:

- show whether the system is doing work right now
- avoid confusion between “nothing happened” and “still running”

Data source:

- `/admin/stream`

No backend addition is strictly required.

This can be maintained entirely in the browser.

---

## 6. Side Effects Summary

A compact summary of non-chat side effects recently produced by the loop.

Suggested summaries:

- memory extractions
- conversation summaries
- world-model updates
- world-model proposals
- scheduled prompt deliveries/skips/failures
- control task creations/reuse

Purpose:

- expose that the loop is not only “thinking” but also updating system state
- make invisible background work visible without opening several tabs

Data source:

- SSE stream events only

This should be intentionally lightweight and recent, not a full history table.

---

## What To Reuse As-Is

The following can be reused with little or no backend change:

### SSE event stream

Reuse:

- existing `/admin/stream`
- existing `ControlEvent` mechanism
- existing event names
- existing ring buffer behavior

### Task data

Reuse:

- `/admin/tasks`
- task steps
- task links
- task status
- summaries

### Scheduler data

Reuse:

- `/admin/scheduler`
- reminders
- scheduled actions
- scheduled prompts
- scheduled prompt runs

### Event rule data

Reuse:

- `/admin/event-rules`

### Existing dashboard patterns

Reuse:

- current tab-bar / tab-panel layout
- current SSE subscription code
- current event badge rendering patterns
- current table/card styling patterns

The new tab should look like part of the existing admin UI, not like a second
mini-app.

---

## Minimal Backend Additions Recommended

The point is to keep these small and structural.

## 1. Extend `/admin/tasks`

Add optional control metadata when present.

Suggested response additions per task:

```json
{
  "control": {
    "phase": "OBSERVE",
    "correlation_key": "rule:...:entity:...",
    "verify_pending": false,
    "waiting_reason": null,
    "last_event": {...},
    "rule_id": "..."
  }
}
```

This is the single most useful backend addition for the tab.

## 2. Extend `/admin/stats`

Add lightweight control-plane fields:

```json
{
  "control_loop": {
    "event_dispatcher_enabled": true,
    "event_bus_size": 0,
    "event_rules_total": 4,
    "control_tasks_active": 2
  }
}
```

These are cheap to compute and immediately useful.

## 3. Optional: expose recent event-rule suppression counts

This is optional, not required for V1.

Could be helpful:

- suppressed by cooldown
- suppressed by quiet hours
- suppressed by value filter

But this can also be inferred from recent SSE events, so do not prioritize it.

---

## What Is Missing Today

Even with the current runtime, some things are still weak or absent.

## 1. Verification loop visibility

Current problem:

- `verify_after_write()` updates cache and may notify the user
- but it does not emit explicit loop-aware verification events

Impact on this tab:

- cannot show verify requested / verify succeeded / verify failed cleanly
- cannot show verification as part of one control-loop item

This is the biggest missing runtime signal for a true loop view.

## 2. Parsed control-task context in admin APIs

The data exists in `Task.context`, but the admin response does not surface it
yet in a structured way.

Impact:

- tab cannot clearly show control phase, last event, waiting reason, or
  verify-pending state

## 3. EventLog is not the primary reusable source

There is an `EventLog` model, but the current system is mostly driven by live
SSE events and durable task/run/scheduler records.

Recommendation:

- do not build this tab around `EventLog`
- use SSE + current durable entities first

## 4. No explicit “goal” field for all active loop items

For many task-driven items, the title/summary is good enough.
For some event-driven items, the system may need to derive a user-friendly goal
from:

- task title
- summary
- event rule name
- latest prompt envelope

This is acceptable for now.

---

## Client-Side Model

The new tab should maintain a lightweight client-side derived state:

- `activeRunsByRunId`
- `recentLoopEvents`
- `latestSideEffects`

Derive them from the SSE stream.

Suggested approach:

1. Subscribe once to `/admin/stream` as the dashboard already does.
2. For the Control Loop tab:
   - filter loop-relevant events
   - update in-flight run map
   - append to recent activity list
   - update lightweight counters in memory
3. On tab open:
   - load `/admin/tasks`
   - load `/admin/scheduler`
   - load `/admin/event-rules`
   - load `/admin/stats`

This avoids new server polling loops beyond what the dashboard already does.

---

## UX Details

### Control items should be readable, not raw JSON

Prefer:

- title
- short summary
- clear badges for phase/status
- short event description
- relative timestamps

Avoid:

- dumping raw task context blobs
- exposing entire event payloads by default

### Progressive disclosure

For each control item:

- show a concise overview by default
- allow expand/collapse for:
  - linked entities
  - last event payload
  - step list
  - correlation key

### Color/badge semantics

Suggested visual semantics:

- running / active: blue
- waiting / blocked: yellow
- complete: green
- failed / suppressed: red
- memory/world side effects: teal or purple

The UI should help scan state, not just decorate it.

---

## Suggested Implementation Sequence

## Phase A: Reuse-only UI shell

Build the tab using current data only:

1. add `Control Loop` tab
2. show top status strip using current endpoints
3. show filtered active tasks
4. show waiting items from tasks/scheduler
5. show recent control activity from SSE
6. show in-flight runs from SSE

This should already be useful.

## Phase B: Minimal backend additions

Add:

1. `control` payload in `/admin/tasks`
2. `control_loop` stats in `/admin/stats`

This will make the tab much more informative without changing runtime behavior.

## Phase C: Better loop signals later

Only after the tab exists:

1. emit verification-related control events
2. surface stronger control correlation in task context
3. optionally add more derived summaries

Do not block the tab on these.

---

## Recommended V1 Scope

For the first version of the tab, build:

- status strip
- active control work
- waiting / blocked
- recent control activity
- in-flight runs

Use:

- `/admin/tasks`
- `/admin/scheduler`
- `/admin/event-rules`
- `/admin/stats`
- `/admin/stream`

Add only:

- parsed `context.control` in `/admin/tasks`
- lightweight control-loop stats in `/admin/stats`

That is enough for a strong first version.

---

## Summary

The new Control Loop tab should be:

- a near-real-time operational overview
- mostly derived from the SSE stream plus existing task/scheduler/admin data
- durable-state-first, not log-first
- lightweight in both runtime cost and implementation scope

The key design decision is:

- **reuse existing events and stored state**

not:

- **add a large new telemetry layer**

The main missing pieces are small:

- surface parsed control-task context
- expose a few control-plane stats
- later add explicit verification loop signals

That makes this tab a good next step for understanding and debugging the new
control-loop behavior without overloading the system.
