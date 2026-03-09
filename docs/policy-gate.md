# Policy Gate

The Policy Gate is a middleware layer that sits between the agent deciding to call a tool and that tool actually executing. It intercepts high-impact actions and may require explicit user confirmation before proceeding.

The goal is **low-risk autonomy**: the agent can act freely on safe operations, but slows down on anything consequential.

---

## Design Principles

- **Declarative** — policies are data, not code. Adding a new policy does not require changing agent logic.
- **Conservative by default** — if a tool call matches no policy, read-only tools (`get_*`, `list_*`, `search_*`) pass through; any other unrecognised tool requires confirmation.
- **Audited** — all gate decisions (pass, blocked, confirmed, denied) are logged.
- **Channel-agnostic** — confirmation is sent via whatever channel the user is on. Telegram uses inline keyboard buttons.

---

## How It Works

```text
Agent proposes tool call
         │
         ▼
evaluate_policy(tool_name, args)
         │
    ┌────┴─────┐
    │          │
  PASS       CONFIRM
    │          │
    ▼          ▼
 Execute    Save PendingAction
 tool       Send inline Yes/No to Telegram
                │
          ┌─────┴──────┐
          │            │
        YES            NO
          │            │
          ▼            ▼
      Execute       Cancel +
      tool          notify user
```

When a confirmation is required:
1. The policy gate saves a `PendingAction` record and sends a Telegram inline keyboard prompt.
2. The pending action is **deleted before** the tool executes (on Yes), so a second press on the same token returns "expired or already handled" immediately — preventing double-execution.
3. If the user does not respond, the pending action expires (no automatic cancellation; the prompt remains but the agent run has already returned).

---

## Homey Tool Pattern

Homey AI Chat Control uses a **meta-tool pattern**:

- `search_tools` — discover which Homey tool handles a given capability
- `use_tool` — execute the discovered tool by name
- `get_home_structure` — read-only: zones, devices, moods
- `get_states` — read-only: current device values
- `get_flow_overview` — read-only: available automations

The policy gate evaluates the **unprefixed** tool name (e.g. `use_tool`, not `homey_use_tool`).

For `use_tool` calls, the confirmation message is built dynamically from the inner tool name (e.g. `"Execute Homey action 'set_light_bedroom'?"`).

---

## Policy Table

Policies are stored in SQLite (`users.db`, table `action_policy`) and seeded from `app/policy/default_policies.py` on every startup. Existing rows are **upserted** — changing `default_policies.py` takes effect on the next restart without manual DB edits.

### Schema

```text
action_policy
├── id
├── name              human-readable label (unique key for upsert)
├── tool_pattern      fnmatch glob matched against unprefixed tool name, e.g. "use_tool"
├── arg_conditions    JSON dict — optional fnmatch patterns on arg values, e.g. {"capability": "alarm_*"}
├── impact_level      "low" | "medium" | "high"
├── requires_confirm  bool
├── confirm_message   shown in the Telegram prompt (overridden dynamically for use_tool)
├── cooldown_seconds  min time between same action (0 = no cooldown; not yet enforced)
├── enabled           bool
└── created_at
```

### Default Policies

| Name | Pattern | Requires confirm |
| --- | --- | --- |
| Homey use_tool | `use_tool` | No |
| Homey search_tools (read-only) | `search_tools` | No |

All other tools that start with `get_`, `list_`, or `search_` pass through without confirmation. Any other unrecognised tool defaults to requiring confirmation.

---

## Confirmation UX (Telegram)

When a confirmation is required, the agent sends an inline prompt:

> Execute Homey action 'set_thermostat_living_room'?
>
> [✅ Yes]  [❌ No]

The token in the button payload ties the press back to the pending action. The pending action is atomically deleted on the first Yes press — any subsequent press returns "expired or already handled".

---

## Conversational Confirmation (Agent-level)

For high-impact operations that involve many devices, the agent itself asks in chat **before** calling any tools. This is configured in `prompts/instructions.md`:

- Operations affecting all devices in a zone, a floor, or the whole house
- Arming, disarming, or triggering an alarm
- Locking or unlocking a door
- Any change involving 3 or more separate device actions at once

Example: *"I'm going to turn off all 6 lights in the house. Should I go ahead?"*

For single-device operations the agent executes immediately without announcing confirmation.

---

## Impact Levels

| Level | Description | Default behaviour |
| --- | --- | --- |
| `low` | Read-only or easily reversible | Execute immediately, no confirmation |
| `medium` | Affects a single device, reversible | Execute immediately; log prominently |
| `high` | Affects many devices, security-related, or hard to reverse | Always confirm |

---

## Extending Policies

To add a new policy that ships with the app, add an entry to `app/policy/default_policies.py`. It will be upserted on the next startup.

To add a policy at runtime, update the `action_policy` table directly or via a future admin command.

Policy evaluation order: confirmation-required policies first, then alphabetical by name. First matching enabled policy wins.
