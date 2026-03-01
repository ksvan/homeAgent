# Policy Gate

The Policy Gate is a middleware layer that sits between the agent deciding to call a tool and that tool actually executing. It intercepts high-impact actions and requires explicit user confirmation before proceeding.

The goal is **low-risk autonomy**: the agent can act freely on safe operations, but slows down on anything consequential.

---

## Design Principles

- **Declarative** — policies are data, not code. Adding a new policy does not require changing agent logic.
- **Conservative by default** — if a tool call matches a high-impact pattern and the policy is ambiguous, require confirmation.
- **Audited** — all gate decisions (pass, blocked, confirmed, denied) are logged to `agent_run_log`.
- **Channel-agnostic** — confirmation is sent via whatever channel the user is on. Telegram uses inline keyboard buttons.

---

## How It Works

```text
Agent proposes tool call
         │
         ▼
PolicyGate.evaluate(tool_name, args, user, context)
         │
    ┌────┴─────┐
    │          │
  PASS       CONFIRM
    │          │
    ▼          ▼
 Execute    Send confirmation prompt to user
 tool       (inline Yes/No button in Telegram)
                │
          ┌─────┴──────┐
          │            │
        YES            NO
          │            │
          ▼            ▼
      Execute       Cancel + notify
      tool          user of cancellation
          │
          ▼
      Log outcome
```

Confirmation has a **timeout** (default: 60 seconds). If the user does not respond, the action is cancelled and the user is notified.

---

## Policy Table

Policies are stored in SQLite (`users.db`, table `action_policy`) and can be updated at runtime without restart.

### Schema

```text
action_policy
├── id
├── name              human-readable label
├── tool_pattern      glob/regex matched against tool name, e.g. "homey_*"
├── arg_conditions    JSON — optional conditions on args, e.g. {"capability": "alarm_*"}
├── impact_level      "low" | "medium" | "high"
├── requires_confirm  bool
├── confirm_message   template string shown to user, e.g. "Turn off all lights?"
├── cooldown_seconds  min time between same action (0 = no cooldown)
├── enabled           bool
└── created_at
```

### Default Policies (shipped with the app)

| Name | Pattern | Condition | Requires confirm |
| --- | --- | --- | --- |
| Whole-home lights off | `homey_device_set_capability` | zone=all, onoff=false | Yes |
| Whole-home heating off | `homey_device_set_capability` | capability=target_temperature, zone=all | Yes |
| Water shutoff | `homey_device_set_capability` | capability=water_* | Yes |
| Alarm/security device | `homey_device_set_capability` | capability=alarm_* | Yes |
| Door unlock | `homey_device_set_capability` | capability=lock_mode, value=unlocked | Yes |
| Share info with family | `send_message` | target=other_user | Yes |
| Trigger whole-home flow | `homey_flow_trigger` | scope=global | Yes |
| Single light control | `homey_device_set_capability` | capability=onoff, zone=single | No |
| Brightness / colour | `homey_device_set_capability` | capability=dim or light_* | No |
| Read device state | `homey_device_get_state` | — | No |
| Set reminder (self) | `set_reminder` | target=self | No |
| Set reminder (other) | `set_reminder` | target=other_user | Yes |
| Web search | `search_web` | — | No |

All default policies can be overridden or disabled via admin commands or directly in the DB.

---

## Confirmation UX (Telegram)

When a confirmation is required, the agent sends a message like:

> **Action requires confirmation**
> I'm about to: *Turn off all lights in the house*
> This was requested by: Kristian
>
> [✅ Yes, do it]  [❌ Cancel]

The inline buttons capture the response. The token ties the button press back to the pending action.

If the requester is not the household admin and the action affects the whole home, the confirmation is sent to both the requester and the admin (configurable).

---

## Impact Levels

| Level | Description | Default behaviour |
| --- | --- | --- |
| `low` | Read-only or easily reversible | Execute immediately, no confirmation |
| `medium` | Affects a single device, reversible | Execute immediately; log prominently |
| `high` | Affects many devices, security-related, or hard to reverse | Always confirm |

---

## Cooldowns

A cooldown prevents the same action from being triggered repeatedly in a short window. Example: if the whole-home heating-off policy has a 5-minute cooldown, and someone triggers it, a second request within 5 minutes will be blocked and the user told when it can next be triggered.

Cooldowns are tracked in the `agent_run_log`.

---

## Admin Management

Household admins can manage policies via agent commands:

- "Show me all policies" — lists active policies and their confirm status
- "Disable confirmation for single-room light control" — updates policy in DB
- "Add a confirmation requirement for turning on the outdoor lights" — creates new policy

Policy changes are logged to `event_log`.

---

## Extending Policies

To add a new policy in code (for policies that must always ship with the app):

Add an entry to `app/agent/policy_gate/default_policies.py`. Policies defined here are seeded into the DB on first run and on upgrade if missing. User-modified policies are not overwritten.

To add a policy at runtime, use the admin agent command or update the `action_policy` table directly.
