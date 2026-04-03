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

    # 5. Build prompt envelope and call agent_run()
    text = _build_event_envelope(event, rule)
    await agent_run(
        text=text,
        user_id=rule.user_id,
        household_id=event.household_id,
        channel_user_id=rule.channel_user_id,
        trigger="event",
        save_history=False,
    )
```

#### 5. Event rules

`EventRule` records define when an event should wake the agent vs just sync state.

Proposed model (new table in `memory.db` or `cache.db`):

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
2. Add an action card: HTTP POST to `http://homeagent:8080/webhook/homey/event`
3. Set header `X-Homey-Secret: <homey_webhook_secret>`
4. Set body to JSON with `event_type`, `entity_id`, `capability`, `value`, `zone`

No Homey app or SDK changes required. Standard HTTP webhook flow.

---

### Concurrency and safety

- The event dispatcher is a single `asyncio.Task` — no concurrent dispatch
- Each `agent_run()` it spawns acquires the per-user lock, same as all other triggers
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

## Phase 3: Graph semantics (Future, conditional)

Phase 3 is only needed if the event routing / state machine becomes too branchy
to maintain as plain Python.

Current manually-coded states in `TaskStatus`:
`PENDING` → `ACTIVE` → `AWAITING_INPUT` → `AWAITING_CONFIRMATION` → `COMPLETED` / `FAILED`

If the control loop adds states like `OBSERVING`, `DECIDING`, `ACTING`,
`VERIFYING`, `WAITING`, the transition table will grow fast. At that point,
`pydantic-graph` is the preferred escape hatch — it fits the typed Python style
of this repo better than LangGraph.

Trigger for introducing pydantic-graph:
- More than ~6 distinct control states
- Transitions are hard to follow in plain if/elif chains
- State machine logic is being duplicated across modules

Until then: stay with plain Python.

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
