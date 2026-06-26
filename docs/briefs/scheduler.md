# Scheduler Brief

Status: implemented, active runtime
Last code check: 2026-06-26

## Purpose

The scheduler runs reminders, scheduled prompts, task resumes, cleanup jobs,
email retries, and domain refresh jobs. Scheduled work should reuse the same
core runtime path where practical.

## Main Files

- `app/scheduler/jobs.py` - scheduled prompt and task resume execution.
- `app/scheduler/reminders.py` - reminder scheduling.
- `app/scheduler/scheduled_prompts.py` - scheduled prompt helpers.
- `app/scheduler/task_resumes.py` - task resume scheduling.
- `app/scheduler/cleanup.py` - retention cleanup.
- `app/scheduler/wine.py` - wine refresh job.
- `app/tasks/service.py` - task resume state and active task context.
- `app/models/scheduled_prompts.py` and `app/models/tasks.py` - durable records.

## Invariants

- Background agent runs should use `agent_run()` and per-user locking.
- Persist state before scheduling when a lost job would strand a task.
- High-impact scheduled side effects must be policy-gated before queueing.
- Scheduler jobs should be idempotent enough for startup restore/retry behavior.

## Verification

- `uv run pytest tests/unit/test_scheduler_triggers.py -v`
- `uv run pytest tests/unit/test_task_state_machine.py -v`
- `uv run pytest tests/unit/test_task_pursuit.py -v`

## Deeper Docs

- `docs/multi-step-task-design.md`
- `docs/proactive-scheduled-behaviour-design.md`
- `docs/autonomous-task-pursuit-design.md`
- `docs/autonomous-goal-reflection-design.md`
