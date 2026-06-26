# Agent Runtime Brief

Status: implemented, active runtime
Last code check: 2026-06-26

## Purpose

The agent runtime is the single execution path for user messages, scheduled
prompts, and task resumes. It assembles context, runs the PydanticAI agent,
records history/logs, and launches background extraction work.

## Main Files

- `app/agent/runner.py` - unified `agent_run()` path and per-user lock.
- `app/agent/agent.py` - agent definition, prompt assembly, tool registration.
- `app/agent/context.py` - profile, world model, task, memory, and history context.
- `app/agent/llm_router.py` - model/provider selection.
- `app/agent/tools/` - tool implementations registered with the agent.
- `prompts/persona.md` and `prompts/instructions.md` - loaded prompt text.

## Invariants

- Keep one conversational agent; do not add a multi-agent router.
- Slash commands stay outside the LLM path.
- High-impact side effects must pass through the policy gate.
- Live device state should be queried through tools, not preloaded into prompts.
- Always-loaded prompt/context additions must stay compact.

## Verification

- `uv run pytest tests/unit/ -v --tb=short`
- `uv run ruff check app/`
- For broad runtime changes, also run `just check-ci`.

## Deeper Docs

- `docs/agent-design.md`
- `docs/architecture.md`
- `docs/policy-gate.md`
- `docs/autonomy-control-loop-design.md`
