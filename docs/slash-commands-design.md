# Slash Commands Design (Pre-LLM Command Layer)

## Purpose

Add a lightweight command layer for user/admin actions that should run in chat **without invoking the LLM**.

Commands are triggered by a leading `/` (for example `/contextstats`), and are intended for deterministic, low-cost operational tasks.

## Placement in Flow

Intercept slash commands at the latest point before LLM execution:

1. Message received
2. Access/rate-limit checks
3. User lookup/create
4. Context assembly (`assemble_context`)
5. Agent/toolset initialization (if needed by command handlers)
6. **Slash command dispatch**
7. If command handled: return response directly (no `run_conversation`)
8. Else: continue normal LLM flow

This preserves full runtime context while avoiding model cost for command operations.

## Command Contract

Implement a small command framework:

- `SlashCommandContext`
  - `raw_text: str`
  - `user_id: str`
  - `telegram_id: int`
  - `is_admin: bool`
  - `household_id: str`
  - `agent_context: AgentContext`
  - `settings: Settings`
  - Optional service handles (scheduler/channel/agent metadata) as needed

- `SlashCommand` interface
  - `name: str` (without `/`)
  - `help: str`
  - `admin_only: bool = False`
  - `async run(ctx: SlashCommandContext, args: list[str]) -> str`

- `SlashCommandRegistry`
  - Register command handlers in one place
  - Lookup by command name
  - Provide list for `/help`

- Parser rules
  - Syntax: `/command [args...]`
  - First token = command name, remaining = args
  - Unknown command returns short error + hint to `/help`

## Permission Model

- Default: commands are available to any allowed user.
- Admin-only commands require `user.is_admin == True`.
- If non-admin invokes admin command:
  - Return concise denial message.
  - Do not invoke LLM fallback.
- Mark command visibility in `/help`:
  - Show all user commands.
  - Show admin commands only to admins.

## Initial Command List (v1)

### User commands

- `/help`
  - List available slash commands with one-line descriptions.

- `/contextstats`
  - Show context size breakdown used for LLM call preparation:
    - conversation history chars/messages
    - conversation summary chars
    - user profile chars
    - household profile chars
    - relevant memories count/chars
    - estimated total chars/tokens

- `/history [n]`
  - Show what recent conversation history is currently passed to the LLM.
  - Default window summary with optional `n` limit for displayed entries.
  - Include note whether a conversation summary exists.

- `/schedule`
  - Unified list of active reminders and scheduled Homey actions for current user.
  - Return compact lines: type, title/description, scheduled time, task ID.

### Admin commands

- `/status`
  - Short operational status snapshot:
    - scheduler running/stopped
    - Homey MCP connected/disconnected
    - Prometheus MCP connected/disconnected
    - basic DB health summary (existing health helpers acceptable)

- `/users`
  - List known users in household with admin flag.

## Response Guidelines

- Deterministic, concise, text-first output.
- No tool-calling language in output.
- No LLM-style suggestions unless explicitly requested.
- Safe failure messages for invalid args and unavailable components.

## Observability

Log slash command execution separately from LLM runs:

- Emit control/event entries for:
  - command start
  - command success
  - command failure
- Include command name, user_id, duration_ms, success flag.

## Extensibility Rules

- New commands should be added as isolated handlers and registered in the registry.
- Keep business logic inside command modules, not in the dispatcher.
- Keep parser and permission checks centralized in dispatcher.
- Command handlers may reuse existing services (scheduler, memory, context, DB sessions) but should avoid side effects unless explicitly intended.

## Non-Goals (v1)

- No nested subcommand framework.
- No shell-like quoting/escaping parser beyond simple whitespace split.
- No replacement of existing Telegram native command handlers yet; this layer is chat-message based and pre-LLM.
