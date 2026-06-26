# Briefs Inventory

Status: current orientation aid
Last code check: 2026-06-26

These briefs are short entry points for agents and humans. Use them before
loading longer design docs when the task is subsystem-specific.

## Available Briefs

- [Agent Runtime](agent-runtime.md) - agent execution, context assembly, LLM routing, tools, prompts.
- [World Model](world-model.md) - canonical household entities and structured grounding.
- [Email Intake](email-intake.md) - AgentMail webhook, queue, preprocessing, Telegram confirmation.
- [Scheduler](scheduler.md) - reminders, scheduled prompts, task resumes, cleanup, background jobs.
- [Control Admin](control-admin.md) - admin API, dashboard, SSE events, control-loop visibility.
- [Flights](flights.md) - flight tracking, provider abstraction, polling, notifications.

## How To Use

1. Read the relevant brief.
2. Open only the linked runtime files needed for the task.
3. Open deeper design docs only when the brief says the implementation is partial
   or when the code path is unclear.
4. Verify with the focused commands named in the brief.
