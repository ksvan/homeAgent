# Slash Commands

A lightweight pre-LLM command layer for deterministic, low-cost operational tasks. Commands are triggered by a leading `/` and return a response without invoking the model.

---

## Placement in Flow

The intercept lives in `app/bot.py`, after user lookup and **before** context assembly:

```text
Message received
  → Rate-limit check
  → User lookup / create
  → *** Slash command dispatch ***   ← here
  → assemble_context()               (embedding API — skipped for most commands)
  → run_conversation()               (LLM call — skipped for all commands)
```

Commands that need context (e.g. `/contextstats`) call `assemble_context()` themselves inside the handler. This avoids the embedding API cost for commands that don't need it.

---

## Module Structure

```text
app/commands/
├── __init__.py      (empty)
├── registry.py      SlashCommandContext, SlashCommand ABC, SlashCommandRegistry
├── dispatcher.py    try_dispatch() — parse, permission check, emit event
└── handlers.py      All built-in command implementations + module-level registry
```

---

## Command Contract

### `SlashCommandContext` (dataclass)

```python
raw_text: str        # full original message
args: list[str]      # tokens after the command name
user_id: str
user_name: str
telegram_id: int
is_admin: bool
household_id: str
```

`AgentContext` and `Settings` are **not** included — handlers import what they need directly to keep the interface minimal and avoid the embedding API cost for commands that don't require context.

### `SlashCommand` (ABC)

```python
name: str           # command name without /
help: str           # one-line description shown in /help
admin_only: bool    # default False

async def run(self, ctx: SlashCommandContext) -> str
```

### `SlashCommandRegistry`

```python
def register(cmd: SlashCommand) -> None
def get(name: str) -> SlashCommand | None
def list_visible(is_admin: bool) -> list[SlashCommand]
```

---

## Dispatcher

`try_dispatch(text, user_id, user_name, telegram_id, is_admin, household_id)` in `app/commands/dispatcher.py`:

1. If text does not start with `/` → return `None` (caller falls through to LLM)
2. Parse: first token = command name, remainder = args
3. Lookup in registry → unknown command returns error string (no LLM fallback)
4. Permission check → non-admin calling admin command returns denial string (no LLM fallback)
5. Build `SlashCommandContext`, call `cmd.run(ctx)`
6. Emit `cmd.dispatch` event: `{command, user_id, duration_ms, success}`

---

## Built-in Commands

### User commands

| Command | Description |
| --- | --- |
| `/help` | List all commands visible to the caller (admin commands hidden from non-admins) |
| `/contextstats` | Assemble context and show char/token breakdown per component |
| `/history [n]` | Show last n messages from conversation history (default 10, max 40) |
| `/schedule` | List active reminders and scheduled Homey actions for the current user |

### Admin commands

| Command | Description |
| --- | --- |
| `/status` | Operational status — scheduler, Homey MCP, Prometheus MCP |
| `/users` | List household members with admin flags |

---

## Observability

A single `cmd.dispatch` event is emitted after each command completes:

```json
{
  "command": "history",
  "user_id": "...",
  "duration_ms": 12,
  "success": true
}
```

Visible in the `/admin` SSE stream and event log. Command errors are caught, logged, and returned to the user as a plain error message — the event is still emitted with `"success": false`.

---

## Extensibility

- Add a new class extending `SlashCommand`, implement `run()`, call `registry.register()` at the bottom of `handlers.py`.
- Keep business logic inside command classes, not in the dispatcher.
- The dispatcher owns parsing and permission checks — commands receive a fully-validated context.

---

## Non-Goals (v1)

- No nested subcommands.
- No shell-style quoting beyond whitespace split.
- No replacement of Telegram native command handlers — this layer is message-text based and pre-LLM.
