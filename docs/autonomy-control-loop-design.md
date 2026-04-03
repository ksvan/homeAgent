# Autonomy Control Loop Design

This document covers the design of HomeAgent's native autonomy control loop —
how the agent moves from a purely reactive system (answers questions) toward a
proactive one (observes, decides, acts, verifies, waits).

It covers all three phases and is written to be reviewed by Codex.

---

## Background and Motivation

HomeAgent has rich execution primitives:

- `assemble_context()` — full context assembly before each LLM call
- `run_conversation()` — the conversation execution core
- `TaskService` + APScheduler — deferred and resumable multi-step work
- `fire_scheduled_prompt()` — existing autonomous execution path
- Policy gate + confirmation flow — safety boundary at tool boundaries
- Event emission + `run_id` threading — observability

Before Phase 1, the control logic was scattered: `bot.py` handled user messages
differently from `jobs.py` handling scheduled prompts and task resumes. There
was no shared locking, no unified retry logic, and no common run record.

The goal of the control loop work is to make the agent's execution paths
explicit, unified, and eventually event-driven — without redesigning the
single-agent runtime.

---

## Phase 1: Unified Execution Path (Implemented)

### What was built

`app/agent/runner.py` provides:

- `agent_run()` — the single entry point for all agent execution
- `get_user_run_lock()` — per-user `asyncio.Lock` keyed by user UUID

All trigger paths now go through `agent_run()`:

| Trigger              | Source                               | Lock |
|----------------------|--------------------------------------|------|
| User message         | `bot.py` → `agent_run()`             | Yes  |
| Task resume          | `scheduler/jobs.py` → `agent_run()`  | Yes  |
| Scheduled prompt     | `scheduler/jobs.py` → `agent_run()`  | Yes  |

### What `agent_run()` does

1. Resolves `user_name` / `household_name` from DB if not provided
2. Calls `assemble_context()` — full prompt + profile + world model + memory
3. Emits `run.start` event
4. Calls `run_conversation()` with retry logic for transient errors
5. Emits `run.complete` or `run.error`
6. Writes `AgentRunLog` to cache DB
7. Optionally saves conversation turn (controlled by `save_history` flag)
8. Fires background tasks (`verify_after_write` etc.)

### Key flags

- `trigger=` — "user_message" | "task_resume" | "scheduled_prompt" | "event"
- `save_history=` — True for user messages and task resumes, False for scheduled prompts
- `retries=` — retry count for transient errors (status 429/5xx)
- `on_retry=` — async callback to notify user on retry

### What this fixed

- Concurrent jobs for the same user now queue behind the per-user lock
- All runs produce `AgentRunLog` records visible in the admin dashboard
- Retry logic is consistent across all trigger types
- Context assembly is consistent across all trigger types

---

## Phase 2: Sensing / Inbound Events (Next)

### The gap

After Phase 1, the agent can be woken by:
- User messages (Telegram)
- Timers (APScheduler)

It cannot be woken by:
- Device state changes
- Sensor thresholds being crossed
- Homey flow triggers
- Any external world-state change

Without inbound events, the "observe household conditions" part of the control
loop is not achievable beyond timer-based polling.

### Goal

Add the minimum infrastructure needed so the control loop can be triggered by
external events, not only by timers and user messages.

### Non-goals for Phase 2

- No complex event processing rules DSL
- No event persistence / replay
- No multi-source fan-in beyond Homey
- No calendar webhooks (later)
- No autonomous self-spawning loops
- No new agent or orchestration layer

---

### Design

#### 1. Inbound event schema

```python
@dataclass
class InboundEvent:
    source: str          # "homey", "internal", future: "calendar"
    event_type: str      # "device_state_change", "flow_trigger", "threshold"
    household_id: str
    entity_id: str       # device_id, zone_id, etc.
    payload: dict        # event-specific data
    timestamp: datetime
    raw: dict            # original payload for audit
```

#### 2. Event receiver endpoint

Add `POST /webhook/homey/event` to `app/api/webhooks.py`.

Authentication: shared secret token in header, same pattern as Telegram webhook.
The secret is `settings.homey_webhook_secret` (new env var).

The endpoint:
1. Validates the secret
2. Parses the body into an `InboundEvent`
3. Enqueues it on the event bus
4. Returns `{"ok": true}` immediately (no blocking)

This means Homey Advanced Flows can push events to HomeAgent by calling this
endpoint when a trigger fires (device capability change, virtual button, etc.)

Payload contract (Homey side sends):

```json
{
  "event_type": "device_state_change",
  "entity_id": "device-uuid",
  "entity_name": "Living Room Motion",
  "capability": "alarm_motion",
  "value": true,
  "zone": "Living Room"
}
```

`household_id` is not in the payload. HomeAgent is a single-household system,
so the webhook endpoint resolves it server-side by querying the one `Household`
record in `users.db`. If the household cannot be found (e.g. first-run before
setup completes) the endpoint returns 503 and logs an error.

#### 3. Event bus

A module-level `asyncio.Queue[InboundEvent]` in `app/control/event_bus.py`.

```python
_event_bus: asyncio.Queue[InboundEvent] = asyncio.Queue(maxsize=256)

def enqueue_event(event: InboundEvent) -> None:
    """Non-blocking enqueue. Drops event if bus is full (back-pressure safety)."""
    try:
        _event_bus.put_nowait(event)
    except asyncio.QueueFull:
        logger.warning("Event bus full — dropping event: %s/%s", event.source, event.event_type)

async def get_event() -> InboundEvent:
    return await _event_bus.get()
```

Keep it simple: in-process, no persistence, ephemeral. If the process restarts
events are lost — that is acceptable. Events are state signals, not commands.

#### 4. Event dispatcher

A long-running background task started in `_lifespan` in `app/api/server.py`.

`app/control/dispatcher.py`:

```python
async def run_event_dispatcher() -> None:
    """
    Background loop: dequeue InboundEvents and route them.
    Runs for the lifetime of the process.
    """
    while True:
        event = await get_event()
        try:
            await _dispatch(event)
        except Exception:
            logger.exception("Event dispatch error for %s/%s", event.source, event.event_type)


async def _dispatch(event: InboundEvent) -> None:
    # 1. Always sync world model / state cache
    await _sync_world_state(event)

    # 2. Evaluate whether this event should trigger an agent run
    rule = await _match_event_rule(event)
    if rule is None:
        return  # no rule matched — sync only, no agent wake

    # 3. Apply cooldown / dedup
    if _is_on_cooldown(event, rule):
        return

    # 4. Respect quiet hours if rule has them
    if _in_quiet_hours(rule):
        return

    # 5. Build prompt envelope and fire agent_run() as a background task.
    # Two reasons for create_task rather than await:
    #   a) Prevents head-of-line blocking: the dispatch loop keeps draining
    #      events (including pure state-sync events) while the agent runs.
    #   b) agent_run() callers are responsible for acquiring the per-user lock
    #      (see runner.py). Acquiring it inside the dispatch loop would stall
    #      all event processing for the duration of the lock.
    text = _build_event_envelope(event, rule)

    async def _run() -> None:
        async with get_user_run_lock(rule.user_id):
            await agent_run(
                text=text,
                user_id=rule.user_id,
                household_id=event.household_id,
                channel_user_id=rule.channel_user_id,
                trigger="event",
                save_history=False,
            )

    asyncio.create_task(_run())
```

#### 5. Event rules

`EventRule` records define when an event should wake the agent vs just sync state.

Proposed model (new table in `users.db`, same as `Task` and `ScheduledPrompt` —
durable household operating config belongs there, not in recall or cache storage):

```text
EventRule
├── id
├── household_id
├── user_id              — which user to notify / run for
├── channel_user_id
├── name                 — human label, e.g. "Motion alert after 22:00"
├── source               — "homey" (future: "calendar")
├── event_type           — "device_state_change" | "flow_trigger" | "*"
├── entity_id            — specific device/entity, or "*" for any
├── capability           — specific capability filter, nullable
├── value_filter_json    — optional: {"eq": true}, {"gt": 22.5}, etc.
├── condition_json       — optional: quiet_hours, day_of_week, etc.
├── cooldown_minutes     — minimum time between triggers, default 5
├── prompt_template      — what to tell the agent, e.g. "Motion detected in {zone}.
│                          Is anyone expected home now?"
├── enabled
└── created_at
```

V1 rules are created manually (admin API or direct DB insert). The agent can
create rules later once the infrastructure is stable.

#### 6. World model sync (lightweight path)

Even when no EventRule matches, the event should update the state cache:

```python
async def _sync_world_state(event: InboundEvent) -> None:
    if event.event_type == "device_state_change":
        upsert_snapshot(
            event.household_id,
            event.entity_id,
            event.payload.get("capability", ""),
            str(event.payload.get("value", "")),
            source="event",
        )
```

This is the same `upsert_snapshot` already used by `verify_after_write` and
`update_snapshots_from_tool_calls`. The event path just adds a third writer.

#### 7. Prompt envelope for event-triggered runs

When an event triggers `agent_run()`, the text passed should be structured:

```
## Event Trigger
- source: homey
- event_type: device_state_change
- entity: Living Room Motion (device-uuid)
- capability: alarm_motion → true
- zone: Living Room
- time: 23:14

## Rule
Motion alert after 22:00

## Task
Motion detected in the Living Room at 23:14. Is anyone expected home?
If this is unexpected, send a brief heads-up.
```

This gives the agent full context about why it was woken.

---

### Configuration changes

New settings:

```python
homey_webhook_secret: str = ""          # shared secret for /webhook/homey/event
event_dispatcher_enabled: bool = True   # feature flag
```

---

### Integration with the lifespan

In `app/api/server.py` `_lifespan`:

```python
# Start event dispatcher
if settings.event_dispatcher_enabled:
    from app.control.dispatcher import run_event_dispatcher
    _dispatcher_task = asyncio.create_task(run_event_dispatcher())

yield

# On shutdown:
if settings.event_dispatcher_enabled:
    _dispatcher_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await _dispatcher_task
```

---

### How to wire Homey

The expected setup is:
1. Create a Homey Advanced Flow with a trigger (device capability, virtual button, etc.)
2. Add an action card: HTTP POST to `http://<homeagent-lan-ip>:8080/webhook/homey/event`
   (use the LAN IP or hostname of the machine running HomeAgent — the Docker-internal
   hostname `homeagent` is not reachable from a Homey box on the LAN)
3. Set header `X-Homey-Secret: <homey_webhook_secret>`
4. Set body to JSON with `event_type`, `entity_id`, `capability`, `value`, `zone`

No Homey app or SDK changes required. Standard HTTP webhook flow.

---

### Concurrency and safety

- The event dispatcher is a single `asyncio.Task` draining the queue sequentially
- State sync (`_sync_world_state`) is always done inline — never blocked by agent runs
- `agent_run()` is fired as `asyncio.create_task()` so the dispatch loop keeps draining
- Each spawned agent task acquires `get_user_run_lock(rule.user_id)` before calling `agent_run()`, maintaining the same lock contract as all other triggers
- `QueueFull` drops events instead of blocking — bus is a signal system, not a queue
- Cooldown per rule prevents a chatty device from hammering the agent
- The `event_dispatcher_enabled` flag allows disabling without code changes

---

### Verification criteria for Phase 2

- POST a test event to `/webhook/homey/event` → confirm state cache updated
- POST an event matching an EventRule → confirm agent_run() fires with trigger="event"
- POST same event twice within cooldown window → confirm second run suppressed
- POST event during quiet hours → confirm run suppressed
- POST 257 events rapidly → confirm overflow logged, bus does not block
- Restart process mid-queue → confirm graceful loss (expected behavior)

---

## Phase 3: Task-centric closed-loop control

### Phase 3 goal

Move from:

```
event → rule → one agent run
```

to:

```
event → rule → resolve/create control task → agent run
      → action or wait → verification/internal event
      → continue task → complete
```

The desired outcome is that HomeAgent can own a small autonomous issue over
time, not just react once.

Example scenarios:

- Repeated motion alerts late at night → single tracked issue with follow-ups
- Temperature threshold exceeded and later normalised → complete when resolved
- Device action requested → verify effect → retry or confirm success
- Recurring event-driven monitoring until a condition clears

### What Phase 2 still does not solve

Phase 2 is a trigger-and-run model. The gaps it leaves:

- Event-triggered runs are fire-and-forget with no durable linkage to a task
  unless the agent creates one ad hoc.
- No distinction between "notify once" and "track this ongoing issue".
- Cooldown is in-memory only — resets on restart, unreliable for autonomous loops.
- No correlation: if the same device fires twice, the second run has no
  awareness of the first.
- `verify_after_write()` updates the cache and may warn the user, but does not
  feed back into the control loop as structured input.

### Design principles

1. Reuse `Task` as the durable control-cycle container.
2. Reuse `agent_run()` as the only agent execution path.
3. Treat external and internal signals the same: as `InboundEvent`s.
4. Keep high-level `TaskStatus` as-is; add finer loop phase inside task context.
5. Prefer explicit correlation and task reuse over spawning new standalone runs.
6. Keep Phase 3 plain Python and service-oriented. Do not adopt pydantic-graph
   yet. Reassess if more than ~6 distinct control states emerge or transitions
   become hard to follow in plain if/elif chains.

---

### Phase 3a: Correlation and task-loop mode

#### 1. Add `run_mode` to `EventRule`

Today an `EventRule` means "if this matches, wake the agent". Phase 3 makes
the intent explicit.

New field:

```text
EventRule
├── ...
├── run_mode              "notify_only" | "task_loop"   default "notify_only"
├── task_kind_default     nullable; "track" or "admin"
└── correlation_key_tpl  nullable; default "rule:{rule_id}:entity:{entity_id}"
```

Meanings:

- `notify_only` — wake the agent with an event envelope. No durable control
  task unless the agent chooses to create one. This is the current Phase 2
  behaviour and remains the default.
- `task_loop` — resolve or create a durable control task; keep subsequent
  relevant events attached to that task.

Note: a `sync_only` mode was considered but is redundant — state sync already
happens unconditionally for all events before rule evaluation.

#### 2. Use `Task` as the durable control-cycle record

Do not introduce a new `ControlLoop` table. Use existing `Task` rows.

- `task_kind="track"` for real-world conditions being monitored over time.
- `task_kind="admin"` for internal/autonomous runtime work not directly
  user-initiated.

Store control-specific state in `Task.context`:

```json
{
  "control": {
    "rule_id": "…",
    "run_mode": "task_loop",
    "phase": "OBSERVE",
    "correlation_key": "rule:<id>:entity:<id>",
    "last_event": {},
    "expected_effect": null,
    "waiting_reason": null,
    "verify_pending": false
  }
}
```

#### 3. Loop phases inside task context

Keep existing `TaskStatus` values unchanged:
`ACTIVE`, `AWAITING_INPUT`, `AWAITING_CONFIRMATION`, `COMPLETED`, `FAILED`, `CANCELLED`

Add a finer phase field in `context["control"]["phase"]`:

```
OBSERVE   → event received, agent not yet run
DECIDE    → agent evaluating what to do
ACT       → agent issuing a device command or action
VERIFY    → waiting for verification result
WAIT      → deferring until a future event or time
DONE      → ready to complete
FAILED    → loop failed, terminal
```

Mapping:

- Task `ACTIVE` can correspond to `OBSERVE`, `DECIDE`, `ACT`, or `VERIFY`
- Task `AWAITING_INPUT` / `AWAITING_CONFIRMATION` corresponds to `WAIT`
- Terminal task statuses remain the source of truth for completion/failure

#### 4. Event dispatch resolves tasks before waking the agent

For `run_mode="task_loop"`:

1. Event arrives
2. Dispatcher matches `EventRule`
3. Dispatcher computes the correlation key (from `correlation_key_tpl` or default)
4. `loop_service.py` looks for an existing active `Task` with that correlation key
5. If found: merge event into that task's context → wake agent against that task
6. If not found: create a new control task, link relevant entities → wake agent

This is the key change from Phase 2. Today the event causes a run. In Phase 3,
the event first resolves whether there is an ongoing loop.

#### 5. New modules

`app/control/loop_service.py`:

- `resolve_or_create_control_task(event, rule) → Task`
- compute correlation key
- merge event payload into task context
- set initial loop phase to `OBSERVE`

`app/tasks/service.py`:

- add `find_active_by_correlation_key(household_id, correlation_key) → Task | None`

#### 6. Fix cooldown persistence gap

The current in-memory `_rule_last_triggered: dict[str, datetime]` resets on
restart. This is acceptable for simple notification rules but unreliable for
autonomous loops that should respect cooldown across restarts.

Fix: add `last_triggered_at: datetime | None` column to `EventRule`. The
dispatcher writes it on trigger. On startup it reads from DB instead of
defaulting to None. This requires an Alembic migration.

---

### Phase 3b: Verification loop closure

#### 7. Thread correlation context through `agent_run()`

When the dispatcher fires `agent_run()` for a control task, pass the task ID
so downstream components (tools, verify) know which loop this run belongs to.

`agent_run()` gains an optional `control_task_id: str | None = None` parameter.
The value is threaded into the tool call context so `verify_after_write()` can
emit the result back to the correct task.

This is the hardest part of Phase 3b. It requires the MCP tool boundary to
pass context back to the verify callback — options:

- Pass `control_task_id` as a request-scoped context var (preferred for clean
  separation).
- Alternatively, look up the active control task in `verify_after_write()` by
  `user_id` + `entity_id` (simpler, no threading needed, but less precise).

V1 can use the lookup approach. Threading via context var is a Phase 3b
refinement.

#### 8. Convert `verify_after_write()` into a loop signal producer

Today `verify_after_write()` reads state back, updates snapshots, and may
notify the user. Phase 3b additionally makes it emit an internal event:

```python
InboundEvent(
    source="internal",
    event_type="verify_result",
    household_id=household_id,
    entity_id=device_id,
    payload={
        "capability": capability,
        "expected": expected_value,
        "observed": actual_value,
        "ok": actual_value == expected_value,
        "control_task_id": control_task_id,  # nullable
    },
    timestamp=datetime.now(timezone.utc),
    raw={},
)
```

This is enqueued on the event bus and dispatched like any other event.

New module `app/control/internal_events.py`:

- `emit_verify_result(...)` — helper for creating and enqueuing the above

#### 9. Verification updates task phase

When a `verify_result` event arrives and a matching control task exists:

- Move task phase to `VERIFY`
- Agent can decide:
  - `ok=True` → advance to DONE, complete task
  - `ok=False` → retry action, notify user, or move to WAIT for re-check
  - ambiguous → ask user or schedule re-check

This should be explicit in task context, not only implied by prose.

---

### Agent contract in Phase 3

When the runtime has resolved an event into a control task (`run_mode="task_loop"`),
the agent sees:

- event envelope (the prompt text)
- active task context including `context["control"]`
- linked entities
- current loop phase

The agent should then:

- update the task and continue (`update_task_progress`)
- schedule a resume (`schedule_task_resume`)
- await user input / confirmation (`await_task_input`)
- complete the task (`complete_task`)
- fail/cancel the task (`cancel_task`)

Autonomous runs should not be saved to user conversation history by default.
`save_history=False` remains the correct setting for event-triggered and
task-loop runs.

The agent's `prompts/instructions.md` needs a Phase 3 section covering
task-first semantics for event-triggered runs once the runtime is in place.

---

### Observability additions for Phase 3

Extend the admin event stream with control-specific events:

- `control.task_resolved` — existing task found by correlation key
- `control.task_created` — new control task created
- `control.phase_changed` — task phase transition
- `control.verify_requested` — verify_after_write() scheduled
- `control.verify_result` — verify result emitted
- `control.completed` — control task completed
- `control.failed` — control task failed

Admin dashboard additions:

- Filter tasks by `context.control.rule_id`
- Display correlation key, current phase, last event summary, verify_pending flag

---

### Exit criteria for Phase 3

Phase 3 is complete when all of the following are true:

1. A matching rule in `task_loop` mode reuses an existing active task when the
   same issue reoccurs (same correlation key).
2. If no matching task exists, the runtime creates one automatically.
3. Event-triggered autonomous work can survive across multiple runs using the
   task system, not only prompt text.
4. `verify_after_write()` can emit an internal verification event back into the
   control loop.
5. A control task can move through observe → decide → act → verify → wait →
   complete using explicit stored phase state.
6. Admin UI can show what autonomous loop is active and why.
7. Cooldown is durable across restarts (via `last_triggered_at` on `EventRule`).

---

### Recommended implementation sequence

1. Add `run_mode`, `task_kind_default`, `correlation_key_tpl` to `EventRule`
   (schema + Alembic migration)
2. Add `last_triggered_at` to `EventRule` (Alembic migration, fixes cooldown
   persistence gap)
3. Add `find_active_by_correlation_key()` to `TaskService`
4. Implement `loop_service.py` (resolve/create control task)
5. Wire dispatcher to use `loop_service` for `run_mode="task_loop"`
6. Add loop phases to `context["control"]`
7. Implement `internal_events.py` and extend `verify_after_write()` (Phase 3b)
8. Thread `control_task_id` through to verification (Phase 3b)
9. Update `prompts/instructions.md` for task-first agent semantics
10. Improve admin visibility

After completing these steps, reassess whether `pydantic-graph` adds enough
value to justify adoption.

---

## Cross-cutting concerns

### Per-user lock coverage

All execution paths acquire `get_user_run_lock(user_id)` before calling
`agent_run()`. This applies to:

- User messages (bot.py)
- Task resumes (jobs.py)
- Scheduled prompts (jobs.py)
- Event-triggered runs (dispatcher.py, Phase 2)

This guarantees no interleaving per user across all trigger types.

### verify-after-write feedback loop

`verify_after_write()` in `app/homey/verify.py` currently:
1. Waits N seconds
2. Reads device state back
3. Updates state cache
4. Notifies user on mismatch

This is fire-and-forget. It does not feed results back into the agent or task
system as structured input.

Design decision: leave this as-is for now. The Phase 2 event bus could
eventually receive the verification result as an internal event
(`source="internal"`, `event_type="verify_result"`), which would close the loop
properly. That is a Phase 2b extension, not core Phase 2.

### Admin dashboard

Phase 2 should add to the admin event stream:
- `event.received` — raw event arrival
- `event.synced` — world state updated
- `event.triggered` — agent_run() called
- `event.suppressed` — cooldown / quiet hours / no rule

And a new admin endpoint:
- `GET /admin/event-rules` — list all EventRule records
