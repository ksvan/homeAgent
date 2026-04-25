# Phase 5: Goal Contracts and Reflection Loop

> **Status**: Design reviewed by Codex and owner. Scope narrowed and decisions
> recorded. Ready for V1 implementation.

---

## Problem

The current autonomous pursuit system tracks *attempts* and *approaches* but has no
formal record of what success looks like, and nothing verifies that it was reached.
Two gaps:

1. **No goal contract.** When a task is created, the agent writes a title and steps,
   but there is no explicit "done means X" statement. The agent can drift from the
   original intent across retries and replans, and call `complete_task` without
   checking whether the original ask was satisfied.

2. **No reflection pass.** Task completion is self-reported. Nothing compares the
   final state against the original goal. A task can be COMPLETED while the user's
   problem is unresolved.

Goal machinery must not slow down simple requests. The agent needs to remain fast and
direct for conversational, single-step interactions.

---

## Agreed Design (post-Codex review)

### Scope: goal contracts only for V1. Heavy reflection loop deferred.

The existing pursuit layer (`record_task_attempt`, `schedule_task_followup`,
`advance_task_step`, `fail_task`, `replan_task`) is solid. The one remaining gap is a
durable contract for "solved means this." Everything below is narrowed to that.

---

### 1. Goal contract at task creation

Extend `create_task` with three optional fields:

```
intent: str             — original user request, verbatim (immutable anchor)
success_criteria: str   — observable statement of what "done" looks like
acceptance_test: str    — how to verify: what to call, what to check, what to observe
```

All optional at creation time. For short-lived or exploratory tasks the agent may
omit them. For autonomous tasks that will use `schedule_task_followup`, instructions
require them (see §4).

Stored in `Task.context["goal"]`:

```json
{
  "intent": "original user request",
  "success_criteria": "The Homey automation fires and device state changes to X",
  "acceptance_test": "Call get_device_state(device_id) and confirm capability == target",
  "outcome": null
}
```

`intent` is written once and never overwritten by replans — it anchors the agent to
the original ask.

For V1, "verbatim" is best effort. The agent supplies `intent`, and should copy the
user's original wording when available. If it omits intent, the runtime may fall back
to `summary` or `title`. A later runtime-level source-text capture can make this truly
verbatim without relying on the model.

---

### 2. Goal enforcement at `schedule_task_followup`

`schedule_task_followup` checks for `context.goal.success_criteria` before scheduling
an autonomous retry. If it is absent:

**Decision: warn-only.** Log a warning, emit `task.goal_missing`, proceed with
scheduling. Advisory only — relies on instructions and the agent's judgment.

**Future upgrade path**: Promote to hard-reject once all `create_task` call sites
reliably set goal fields and there are no in-flight tasks without them. The right
trigger is observational: if `task.goal_missing` events stop appearing in the admin
stream for a sustained period, the soft guard is no longer needed and can be hardened.
Hard-reject is appropriate when the system is mature enough that a missing goal
contract is always a bug, not a normal case.

---

### 3. Reflection baked into `complete_task` (not a separate tool)

`complete_task` gains two **optional** fields:

```python
complete_task(
    task_id: str,
    summary: str = "",
    goal_met: bool | None = None,
    outcome_note: str = "",
)
```

Runtime behavior:

- Task has **no** `context.goal` → allow normal completion (backward compat).
- Task has `context.goal` and `goal_met is None` → reject: "assess goal_met first."
- `goal_met=False` → reject: "replan, ask the user, or call `fail_task`."
- `goal_met=True` and `outcome_note` is empty → reject: "provide an outcome note."
- `goal_met=True` → store `goal.outcome`, complete normally.

Outcome shape:

```json
{
  "goal_met": true,
  "completion_basis": "criteria_met",
  "note": "The automation fired and the device state matched the expected value.",
  "checked_at": "2026-04-25T12:00:00+02:00",
  "run_id": "..."
}
```

`completion_basis` values:

- `criteria_met`: the stated success criteria were satisfied.
- `user_accepted_partial`: the original goal was only partially met, but the user
  explicitly accepted the partial outcome as good enough.
- `superseded_by_user`: the user changed the goal and accepted the new outcome.

Partial success is **not** enough for `goal_met=True` unless the user explicitly
accepts it. Without user acceptance, the agent should replan, ask the user, or fail.

No separate `reflect_on_task` tool for V1. Fewer tool calls, same enforcement.

**Decision: rely on existing retry budget.** If `attempt_count >= max_attempts`,
`schedule_task_followup` is already blocked and the agent is forced to choose between
`fail_task`, `await_task_input`, or completing only if the goal is met or the user
accepts a partial outcome. No new hard guard needed.

For visibility, store a lightweight `goal.completion_rejection_count` whenever
completion is rejected. This is not used for enforcement in V1, but it makes repeated
failed completion attempts visible in admin and can inform later guardrails.

---

### 4. Goal context in active task prompt

`_render_full_task()` in `app/tasks/service.py` injects `context.goal` when present:

```
- original intent: <verbatim request>
- success criteria: <what done looks like>
- acceptance test: <how to verify>
```

`resume_task()` in `app/scheduler/jobs.py` also prepends the goal block to the
resume prompt — re-anchoring the agent on every autonomous wake-up.

---

### 5. Fast path — no classifier needed

No pre-agent classifier. The fast path stays as-is:

- Slash commands bypass the LLM entirely.
- Simple chat and one-shot tool calls respond directly.
- No task = no goal contract, no pursuit, no reflection overhead.

`prompts/instructions.md` will reinforce: *answer simple things directly; only create
goal-backed tasks for multi-step, delayed, autonomous, or result-sensitive work.*

---

### 6. Admin and events

New SSE events:

| Event | Fired when |
|---|---|
| `task.goal_set` | `create_task` called with at least one goal field |
| `task.goal_missing` | `schedule_task_followup` proceeds without `goal.success_criteria` |
| `task.goal_checked` | `complete_task` evaluates `goal_met` (pass or fail) |
| `task.completion_rejected` | `complete_task` rejects because `goal_met=False` or fields missing |

`GET /admin/tasks` exposes `context.goal` alongside the existing `pursuit` field.
Dashboard task detail shows: original intent, success criteria, acceptance test,
outcome, rejection count, and whether the last completion attempt was accepted.

Event payload guidance:

```json
{
  "task_id": "...",
  "goal_met": false,
  "reason": "goal_met_false",
  "outcome_note": "Checked current state; target value was not reached",
  "completion_rejection_count": 1,
  "run_id": "..."
}
```

`task.goal_checked` should fire for both accepted and rejected completion attempts.
`task.completion_rejected` is the explicit admin-stream signal that the task remains
open because the goal contract was not satisfied.

---

### 7. Out of scope for V1: background goal eval

After `task.complete`, a background job could run a cheap LLM call comparing
`goal.intent` + `goal.success_criteria` against `task.summary` + step outcomes,
emitting `task.goal_eval` with `{aligned: bool, note: str}`. Non-blocking, admin-
visible, does not delay the user response. Deferred — V1 relies on agent-side
`goal_met` assertion.

Good time to add this:

- autonomous tasks are completing regularly enough that manual review is tedious
- `task.goal_checked` events show questionable or inconsistent self-assessments
- users/admins report completed tasks that were not actually solved
- the cost of a small non-blocking eval is acceptable compared with the task value
- task summaries and step result notes are consistently populated enough for eval

### 8. Out of scope for V1: structured acceptance tests

`acceptance_test` remains free-form in V1. That keeps the implementation small and
avoids inventing a verification DSL too early.

Later, it may be worth introducing structured fields such as:

```json
{
  "verification_tool": "homey_get_device_state",
  "target_entity_id": "...",
  "expected_state": {"capability": "onoff", "value": true},
  "allowed_observation_window_seconds": 300
}
```

Good time to add this:

- many autonomous tasks use repeated Homey/world-model verification patterns
- free-form acceptance tests produce inconsistent tool use
- admin needs filterable/reportable verification status
- the same acceptance-test shape appears in several task kinds
- hard enforcement becomes desirable before allowing more unattended autonomy

### 9. Out of scope for V1: runtime-captured original intent

V1 stores `goal.intent` from the agent's `create_task` arguments. That is enough to
anchor most tasks, but it is not guaranteed to be verbatim.

Later, the runtime can capture source text directly when resolving or creating a task:

- the current user message for user-triggered tasks
- the structured event envelope for event-triggered tasks
- the scheduled prompt envelope for scheduled autonomous tasks

Good time to add this:

- drift from the original request remains a problem despite goal contracts
- event-triggered tasks need stronger auditability
- admin/review workflows need exact source text
- task creation moves out of direct chat into more autonomous triggers

### 10. Out of scope for V1: hard rejection on missing goals

V1 emits `task.goal_missing` but still allows `schedule_task_followup` to proceed.
This keeps existing and exploratory tasks from getting stuck during rollout.

Good time to hard-reject:

- `task.goal_missing` events have been near-zero for a sustained period
- prompt/tool guidance reliably sets goal fields for autonomous tasks
- there are no important in-flight tasks missing goal contracts
- missing goals are judged to be bugs rather than normal exploratory behavior

---

## What changes (V1)

| File | Change |
|---|---|
| `app/agent/tools/tasks.py` | `create_task`: add `intent`, `success_criteria`, `acceptance_test`; store in `context.goal`; emit `task.goal_set`. `complete_task`: add optional `goal_met`, `outcome_note`; require `outcome_note` when a goal is accepted; store `goal.outcome`; increment `completion_rejection_count` on rejection; emit `task.goal_checked` and `task.completion_rejected`. `schedule_task_followup`: warn and emit `task.goal_missing` if no `success_criteria`. |
| `app/tasks/service.py` | `_render_full_task()`: render `context.goal` when present. |
| `app/scheduler/jobs.py` | `resume_task()`: prepend goal block to resume prompt. |
| `app/control/api.py` | Expose `context.goal` in task detail response. |
| `app/control/dashboard.html` | Show goal fields and outcome in task detail; new event badges. |
| `prompts/instructions.md` | When to create tasks vs. respond directly; how to write goal fields; `goal_met` rules. |

No DB migration — `context` is an existing JSON column.

---

## Decisions log

| Question | Decision |
| --- | --- |
| `schedule_task_followup` enforcement | Warn-only for V1; hard-reject when `task.goal_missing` events consistently stop appearing (see §2) |
| Rejection loop guard in `complete_task` | No hard guard in V1; store `goal.completion_rejection_count` for visibility |
| Partial success | Completion is allowed only if the success criteria are met or the user explicitly accepts the partial outcome |
| `outcome_note` | Required when a goal-backed task completes with `goal_met=True` |
| `intent` capture | Best-effort agent-provided in V1; runtime-captured source text is deferred |
