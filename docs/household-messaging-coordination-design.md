# Household Messaging And Coordination Design

## Purpose

Add a safe household communication layer so HomeAgent can:

1. Send simple messages between household members on request.
2. Coordinate multi-person household tasks by messaging participants, collecting
   replies, reminding when appropriate, and reporting outcomes back to the
   initiating user.

This is not just "let the agent send Telegram messages". Cross-user messaging
has social side effects, privacy implications, and task-state implications. It
should use the same identity, policy, audit, and task discipline as the rest of
the agent.

---

## Example Scenarios

### Simple Household Message

```text
Kristian: Tell Maria I am running 10 minutes late.
Agent: Send this to Maria?
       "Kristian says he is running 10 minutes late."
Kristian: Yes.
Agent -> Maria: Kristian says he is running 10 minutes late.
Agent -> Kristian: Sent to Maria.
```

### Multi-Person Input Collection

```text
Kristian: Ask everyone what they want for dinner and tell me the result.
Agent: I will ask Maria and Ola, wait until 17:00, remind once if needed, and
       report back to you. Should I do that?
Kristian: Yes.
Agent -> Maria: Kristian asked me to collect dinner preferences. What would you like?
Agent -> Ola: Kristian asked me to collect dinner preferences. What would you like?
Maria: Tacos.
Ola: Pasta.
Agent -> Kristian: Maria wants tacos. Ola wants pasta.
```

### Task Continuation Based On Another Member's Reply

```text
Kristian: Ask Maria for the flight booking reference, then continue tracking it.
Agent -> Maria: Kristian asked me to get the booking reference for the flight.
Maria: ABC123.
Agent records response, resumes the flight-monitor setup task, and reports back
to Kristian when the watch is created.
```

---

## Existing Building Blocks

HomeAgent already has most of the substrate:

- `User` and `HouseholdMember` identify people.
- `ChannelMapping(channel="telegram", channel_user_id=...)` resolves users to
  Telegram delivery targets.
- `Task` supports durable state, `task_kind`, `context`, `AWAITING_INPUT`,
  `AWAITING_CONFIRMATION`, and `AWAITING_RESUME`.
- `TaskStep` supports normalized workflow steps.
- `TaskLink` can link tasks to members and other world-model entities.
- APScheduler can handle reminders, timeouts, and resumes.
- The policy gate and pending confirmation pattern already handle explicit user
  approval for side effects.
- The channel abstraction hides Telegram-specific delivery mechanics from core
  agent logic.

The design should extend these rather than introduce a separate workflow engine.

---

## Design Goals

1. Resolve recipients by household identity, not raw Telegram IDs.
2. Make all cross-user messages auditable.
3. Require confirmation for socially meaningful or ambiguous messages.
4. Support task workflows that wait for replies from other members.
5. Route participant replies deterministically when they answer a pending prompt.
6. Report collected outcomes back to the initiating user.
7. Keep participants informed about who initiated a request and why.
8. Preserve privacy boundaries and avoid surprise sharing.
9. Keep V1 implementation small and compatible with the existing task model.

## Non-Goals For V1

- No arbitrary external messaging outside the household.
- No raw Telegram-ID send tool exposed to the LLM.
- No generic survey/form builder.
- No complex branching workflow DSL.
- No hidden long-running coordination unless a user, rule, or scheduled task
  clearly initiated it.
- No end-to-end encryption redesign.
- No message deletion/editing guarantees after delivery.

---

## Layer 1: Household Messaging

Household messaging is the low-level primitive: send one audited message to one
or more known household members.

### Agent Tool

Expose a high-level tool, not a Telegram tool:

```text
send_household_message(
  recipients: list[str],
  message: str,
  reason: str,
  urgency: "normal" | "important" = "normal",
  require_confirmation: bool = true
) -> result
```

`recipients` are member names or stable member/user identifiers. The runtime
resolves them to `User.id` and then to `ChannelMapping(channel="telegram")`.

### Runtime Flow

```text
Agent proposes household message
  -> resolve recipient household members
  -> validate same household and mapped Telegram channel
  -> evaluate messaging policy
  -> create delivery/audit rows
  -> ask initiating user for confirmation if needed
  -> send through Channel.send_message()
  -> mark delivery status
  -> emit admin event
```

### Delivery Rules

- The message must say who initiated it unless the initiator explicitly asks for
  an anonymous-style household announcement and policy allows it.
- The agent may lightly rephrase for clarity, but should not add new claims.
- The agent should not send sensitive personal, medical, financial, or conflict
  messages without explicit confirmation.
- Delivery to all household members should require confirmation.
- Delivery to unmapped members should fail with a useful explanation.

### Proposed Table: `HouseholdMessage`

Store in `cache.db`. This is operational/audit state, not canonical household
knowledge.

```text
householdmessage
├── id
├── household_id
├── initiator_user_id        nullable for system/rule-originated messages
├── recipient_user_id
├── recipient_channel        "telegram" in V1
├── recipient_channel_user_id
├── body
├── reason
├── source_type              "user_request" | "task" | "event_rule" | "scheduled_prompt"
├── source_id                nullable link to task/event/rule/prompt
├── urgency                  "normal" | "important"
├── status                   "pending_confirmation" | "sent" | "failed" | "cancelled"
├── error
├── created_at
└── sent_at
```

This table supports auditing, retries, admin inspection, and later analytics
without scraping Telegram logs.

---

## Layer 2: Household Coordination

Coordination is a stateful workflow built on messaging. It creates a durable
task, sends prompts to participants, tracks replies, optionally reminds, and
returns a result to the initiator.

### Coordination Task Context

Use the existing `Task` table with `task_kind="coordinate"` and structured
`context` JSON:

```json
{
  "coordination": {
    "initiator_user_id": "user_1",
    "report_to_user_id": "user_1",
    "participants": [
      {
        "user_id": "user_2",
        "member_id": "member_2",
        "status": "pending",
        "prompt_id": "prompt_1",
        "responded_at": null
      }
    ],
    "request": "What do you want for dinner?",
    "response_type": "free_text",
    "deadline_at": "2026-05-10T17:00:00+02:00",
    "reminder_policy": {
      "enabled": true,
      "interval_minutes": 60,
      "max_reminders": 1
    },
    "aggregation": "summarize_by_person",
    "completion_policy": "all_replied_or_deadline"
  }
}
```

Recommended task links:

```text
TaskLink(task_id, entity_type="member", entity_id=<initiator_member_id>, role="initiator")
TaskLink(task_id, entity_type="member", entity_id=<participant_member_id>, role="participant")
```

Recommended steps:

```text
TaskStep 0: confirm coordination plan
TaskStep 1: message participants
TaskStep 2: collect responses
TaskStep 3: report outcome
```

### Pending Participant Prompts

The key deterministic routing primitive is a pending prompt per participant.

Proposed table in `cache.db`:

```text
participantprompt
├── id
├── household_id
├── task_id
├── participant_user_id
├── channel
├── channel_user_id
├── prompt_kind              "coordination_response"
├── prompt_text
├── expected_response_type   "free_text" | "yes_no" | "choice" | "number" | "date"
├── status                   "pending" | "answered" | "expired" | "cancelled"
├── reminder_count
├── expires_at
├── created_at
├── answered_at
└── updated_at
```

Incoming Telegram messages should check this table after identity resolution and
before normal chat routing.

### Coordination Responses

Store participant replies separately from normal conversation history:

```text
coordinationresponse
├── id
├── task_id
├── participant_prompt_id
├── participant_user_id
├── channel
├── raw_text
├── normalized_json
├── received_at
└── created_at
```

`normalized_json` can be empty in V1. Later it can hold structured choices,
dates, quantities, or extracted entities.

### Incoming Reply Routing

```text
Telegram message arrives
  -> validate allowlist/rate limit
  -> resolve User
  -> check pending ParticipantPrompt for this user/channel
  -> if exactly one active prompt:
       record CoordinationResponse
       mark prompt answered
       update Task context participant status
       send short acknowledgement
       if completion policy satisfied: summarize and report
       else return without normal LLM chat
  -> if multiple active prompts:
       ask user which request they are answering or choose latest
  -> if no active prompt:
       continue normal chat path
```

This keeps "Tacos" from becoming a normal conversation turn when it is obviously
an answer to a pending dinner coordination prompt.

---

## Agent Tools

### V1 Tools

```text
send_household_message(...)
```

One-off message delivery.

```text
start_household_coordination(
  title: str,
  participants: list[str],
  prompt: str,
  response_type: str = "free_text",
  deadline: str | null = null,
  reminder_policy: dict | null = null,
  report_to: str | null = null
)
```

Creates the task, stores participant prompts, and sends messages after
confirmation/policy approval.

```text
get_coordination_status(task_id: str)
```

Returns who has replied, who is pending, deadline, and current summary.

```text
summarize_coordination_results(task_id: str)
```

Produces a compact result for the initiator.

### Not Exposed To Agent In V1

Do not expose raw helpers like:

```text
send_telegram_message(telegram_id, text)
insert_participant_response(...)
```

Those should be internal service methods only.

---

## Policy And Consent

Cross-user messaging should be treated as a side effect.

### Default Confirmation Matrix

| Scenario | Default |
| --- | --- |
| One-off low-risk message to one named member | Confirm |
| Message to multiple members | Confirm |
| Message to all household members | Confirm |
| Task-created participant prompt after initiator confirmed plan | No extra confirm per participant |
| Reminder inside confirmed coordination task | No extra confirm if within reminder policy |
| Event-rule-originated message | Follow event rule policy |
| Sensitive content | Confirm or refuse |
| Unknown/unmapped recipient | Refuse |

V1 can start with "confirm all cross-user sends" and later relax specific cases.

### Participant Transparency

Participant prompts should include:

- who initiated the request
- what is being asked
- whether the answer will be reported back
- any deadline, if relevant

Example:

```text
Kristian asked me to collect dinner preferences and report back to him.
What would you like for dinner tonight?
```

### Privacy Defaults

- Do not use participant replies as long-term memory by default.
- Do not expose one participant's unrelated profile/memory to another.
- Do not silently forward a private participant reply to the whole household.
- Final report goes to the initiating/report-to user unless the task explicitly
  says it is a shared household summary.

---

## Scheduling

Coordination uses APScheduler for:

- deadline checks
- participant reminders
- task resume after a response

Suggested job IDs:

```text
coordination_deadline:<task_id>
coordination_reminder:<participant_prompt_id>
coordination_report:<task_id>
```

On startup, restore jobs by scanning active coordination tasks and pending
participant prompts.

---

## Admin And Observability

Emit events:

| Event | Meaning |
| --- | --- |
| `message.delivery_requested` | Message row created |
| `message.delivery_confirmed` | Initiator confirmed send |
| `message.sent` | Channel send succeeded |
| `message.failed` | Channel send failed |
| `coordination.created` | Coordination task created |
| `coordination.prompt_sent` | Participant prompt delivered |
| `coordination.response_received` | Participant replied |
| `coordination.reminder_sent` | Reminder delivered |
| `coordination.completed` | Final report generated |
| `coordination.expired` | Deadline reached with incomplete responses |

Admin dashboard should eventually show:

- active coordination tasks
- pending participant prompts
- delivery failures
- final summaries

V1 can rely on the live SSE feed plus task inspection.

---

## Failure Handling

| Failure | Behaviour |
| --- | --- |
| Recipient has no Telegram mapping | Do not send; tell initiator who is unmapped |
| Telegram send fails | Mark message/prompt failed; report to initiator |
| Participant does not reply by deadline | Include "no reply" in final report |
| Multiple pending prompts for participant | Ask clarifying question or attach to latest prompt |
| Initiator cancels task | Cancel pending prompts and future reminders |
| App restarts | Restore pending prompt/deadline/reminder jobs |
| Duplicate callback/message | Idempotently ignore if prompt already answered |

---

## Implementation Plan

### Phase 1: One-Off Messaging

1. Add `HouseholdMessage` model and Alembic migration.
2. Add `app/messaging/service.py` for recipient resolution and delivery.
3. Add `app/agent/tools/messaging.py`.
4. Add conservative policy/confirmation handling.
5. Add admin/live events.
6. Add focused tests for recipient resolution, unmapped users, and audit rows.

### Phase 2: Pending Participant Prompts

1. Add `ParticipantPrompt` and `CoordinationResponse` models.
2. Add pre-agent Telegram routing hook after user resolution.
3. Store replies and acknowledge them without invoking the general agent path.
4. Add scheduler restoration for prompt expirations.

### Phase 3: Coordination Tasks

1. Add `start_household_coordination` tool.
2. Store `Task(task_kind="coordinate")` with structured context.
3. Send participant prompts through the messaging service.
4. Add completion policy checks.
5. Add final report to initiator.

### Phase 4: Reminders And Richer Outcomes

1. Add reminder jobs per participant prompt.
2. Add response types beyond free text: yes/no, choice, date, number.
3. Add admin UI affordances for active coordination tasks.
4. Add optional shared household summary delivery.

---

## Open Questions

1. Should all household messaging require confirmation in V1, or can direct
   one-recipient low-risk messages skip confirmation?
2. Should participants be able to opt out of coordination reminders?
3. Should participant replies be saved to normal conversation history, or only
   to coordination-specific tables?
4. How should the agent handle "ask everyone" when some household members do
   not have Telegram mappings?
5. Should coordination prompts support inline buttons for yes/no and choices in
   V1, or start with plain text only?
6. Should final reports include exact raw replies, LLM summaries, or both?
