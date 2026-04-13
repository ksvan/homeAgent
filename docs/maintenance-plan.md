# Maintenance Plan

This document turns the current maintenance review into a working backlog.
It is intentionally practical: focus on keeping the current architecture healthy
while control-loop, task, and world-model work continues.

## Current Assessment

The app is in a growth phase. Core runtime architecture has improved
substantially, but maintenance needs now center on:

- verification depth
- operational visibility
- migration and dependency discipline
- documentation drift control

The main risk is not obvious instability. The main risk is that runtime behavior
changes faster than tests, health surfaces, and docs keep up.

## Principles

- Prefer strengthening the current runtime over adding parallel maintenance systems.
- Reuse existing admin stats, SSE events, cleanup jobs, and task state where possible.
- Keep prompt files lean; maintenance policy belongs in docs, CI, and local coding guidance.
- When runtime behavior changes, update tests and docs in the same change when feasible.

## Must Do Now

### 1. Widen the safety net

- Expand CI beyond `ruff check app/` and `pytest tests/unit/`.
- Add `mypy app/` to CI.
- Lint `tests/` too, not just `app/`.
- Keep unit-test runtime fast; do not turn CI into a heavy integration pipeline yet.

### 2. Cover the newest risk areas

Add focused tests for:

- unified runner behavior in `app/agent/runner.py`
- event dispatcher rule matching and suppression in `app/control/dispatcher.py`
- control-task reuse/creation in `app/control/loop_service.py`
- task state transitions and resume scheduling in `app/tasks/` and `app/scheduler/jobs.py`
- cleanup retention behavior in `app/scheduler/cleanup.py`

Priority rule:

- test orchestration and lifecycle logic first
- test low-risk formatting/helpers second

### 3. Tighten health and admin visibility

- Keep `/admin/stats` as the main operational summary surface.
- Ensure maintenance checks include:
  - dispatcher running
  - event bus size
  - active control tasks
  - scheduler health
  - MCP connectivity
- Align `/health` with the newer runtime so external health checks do not lag the real system shape.

### 4. Review stale observability surfaces

- Decide whether `EventLog` is still needed.
- If it is not part of the real observability path, remove it rather than carrying dead schema and cleanup logic.
- If it is needed later, document its intended purpose clearly before expanding it.

## Before Phase 3

These items should be done before deeper autonomy/control-loop work expands.

### 1. Migration discipline

- Add a simple release-time check that a fresh DB can migrate to head cleanly.
- Review recent migrations for naming consistency and rollback assumptions.
- Keep schema changes small and grouped by feature.

### 2. Retention policy review

- Move hardcoded task/prompt retention values to config where practical.
- Re-check whether current purge windows are enough for admin/debug use.
- Keep cleanup jobs cheap and predictable.

### 3. Control-loop operability

- Keep the control-loop admin tab reuse-first.
- Do not add a new telemetry store.
- Surface loop state from existing tasks, scheduler state, SSE events, and stats.

### 4. Verification of event-driven behavior

- Add at least one integration-style smoke path for:
  - inbound event
  - rule match
  - task reuse or task creation
  - agent run trigger

This does not need to be a full end-to-end external-system test.

## After Phase 3

### 1. Dependency and upgrade hygiene

- Review APScheduler regularly because the project still depends on a pre-release series.
- Review LLM SDK and PydanticAI changes when upgrading.
- Keep `uv.lock` current and treat dependency upgrades as deliberate maintenance work.

### 2. Remove or simplify leftover legacy paths

- Remove legacy observability or storage concepts that are no longer used.
- Reduce duplicated admin/runtime logic once the control-loop path stabilizes.

### 3. Add broader smoke coverage

- Add a small set of startup and admin smoke tests.
- Add a migration-plus-startup check in CI or release validation.

## Drift Prevention

Use these rules to reduce design drift:

- If a change alters runtime flow, also update the most relevant design doc.
- If a change alters user-visible behavior, update `README.md` or command docs when needed.
- If a change alters always-loaded prompt behavior, keep `prompts/instructions.md` compact and update only decision rules, not long tutorials.
- If a change alters multi-step tasks, proactive behavior, or control loop behavior, review the corresponding docs in `docs/`.
- Prefer one source of truth per topic:
  - code for exact behavior
  - docs for architecture and rationale
  - prompt files for agent decision rules
  - local `CLAUDE.md` for coding-agent guidance

## Suggested Maintenance Cadence

### Every meaningful runtime change

- run targeted tests
- update the most relevant doc if behavior changed
- add changelog note for shipped user-visible/runtime-significant changes

### Weekly or before merging a larger feature batch

- review failing or missing tests in newly changed areas
- scan admin stats and logs for growth issues
- check scheduler cleanup behavior

### Before release or major phase work

- run unit tests, type check, and migration check
- review docs for drift in the areas touched
- review `.env.example` if configuration changed

## Practical Backlog

### Must do now

- Add `mypy` to CI
- Lint `tests/` in CI
- Add runner/dispatcher/control-task tests
- Review `/health` against current control-loop runtime
- Decide fate of `EventLog`

### Before phase 3

- Add migration smoke validation
- Make retention policy less hardcoded
- Add one event-driven smoke path
- Ship the control-loop admin tab

### After phase 3

- Review APScheduler dependency risk
- Remove stale legacy surfaces
- Add broader startup/admin smoke checks


#### Other
- look for packages needing upgrade. E.g. Fastmcp
