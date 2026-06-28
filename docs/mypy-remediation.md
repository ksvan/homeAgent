# Mypy Remediation Plan

## Outcome

The codebase will have zero mypy errors under `strict = true`, and
`uv run mypy app/` will be added as a required gate in CI alongside the existing
ruff lint and format checks.

---

## Background and Reasoning

The project already has mypy configured with `strict = true` in `pyproject.toml`.
This means every function must be fully annotated, generics must be parameterised,
and `Any` escapes must be explicit. Strict mypy at this level catches real
bugs — wrong argument types, None-access without guards, signature drift between
call sites and definitions — before they reach production.

When CI checks were extended to include `ruff format --check` and `mypy`, the
format check was clean after a one-time bulk reformat. Mypy, however, revealed
196 errors across 39 files. These errors had been accumulating silently because
mypy was never wired into CI.

The errors split into two tiers:

- **Mechanical drift** — stale `type: ignore` comments, missing generic
  parameters, and absent type stubs. These are not bugs; they are housekeeping
  debt that accumulated as the codebase grew.

- **Real type mismatches** — SQLModel query patterns that mypy cannot follow
  without guidance, and genuine argument/assignment errors that are likely
  masking latent bugs.

Fixing all of this in one change would be risky and hard to review. A phased
approach lets each phase be reviewed and merged independently, with the error
count as a clear progress metric.

---

## Error inventory (as of 2026-06-26)

| Category | Count | Risk |
|---|---|---|
| Stale / wrong-code `type: ignore` comments | 39 | None — cleanup only |
| Missing generic params (`list`, `dict`) | 38 | None — annotation only |
| SQLModel column method calls (`.desc()`, `.asc()`, `.in_()`) | ~17 | Low |
| SQLModel `exec()` result typed as `object` | ~10 | Low |
| Missing type stubs (`psutil`, `openpyxl`, `PyYAML`) | ~5 | None |
| `no-any-return` on typed functions | 7 | Low |
| Missing module `app.control.admin_events` | 4 | Investigate |
| Real type errors (`arg-type`, `assignment`, `call-arg`) | ~20 | Medium |

**Total: 196 errors across 39 files**

---

## Phase 1 — Zero-logic changes (~80 errors)

**Goal**: clear all errors that require no behaviour change.

### Install missing stubs

```bash
uv add --dev types-psutil types-openpyxl types-PyYAML
```

### Fix stale `type: ignore` comments (39 errors)

Comments where the suppressed error code no longer matches, or where the
underlying issue was fixed and the ignore is now redundant. Each one should
either be removed or updated to the correct `[error-code]`.

Run `uv run mypy app/ 2>&1 | grep "Unused"` to list them precisely.

### Add missing generic parameters (38 errors)

Replace bare `list` and `dict` with parameterised forms:

- `list` → `list[X]`
- `dict` → `dict[K, V]`

These are annotation-only changes. Run
`uv run mypy app/ 2>&1 | grep "type-arg"` to list them.

### Investigate `import-not-found: app.control.admin_events` (4 errors)

Four import sites reference this module. Either it was renamed and imports were
not updated, or it is generated at runtime. Trace each import and fix or add a
`# type: ignore[import-not-found]` with a comment explaining why.

---

## Phase 2 — SQLModel typing patterns (~30 errors)

**Goal**: teach mypy about the SQLModel and SQLAlchemy patterns used throughout
the repository files.

### Column method calls

SQLModel types column fields as their Python type (`datetime`, `str`, etc.),
not as `Column`. This causes `.desc()`, `.asc()`, `.in_()` to fail:

```python
# mypy cannot see .desc() on datetime
order_by(MyModel.updated_at.desc())

# fix: use SQLModel's col() helper
from sqlmodel import col
order_by(col(MyModel.updated_at).desc())
```

Affected files: `flights/repository.py`, `world/repository.py`,
`control/api.py`, `tasks/repository.py`, `wine/sync.py`.

### `exec()` result typed as `object`

`session.exec(select(...))` returns `ScalarResult[Model]` but only when the
select is explicitly typed. Without it, results come back as `object` and
attribute access fails:

```python
# mypy sees object, not FlightWatch
result = session.exec(select(FlightWatch).where(...))

# fix: annotate the select
result: ScalarResult[FlightWatch] = session.exec(
    select(FlightWatch).where(...)
)
```

---

## Phase 3 — Real type errors (~20 errors)

**Goal**: fix genuine type mismatches. These are most likely to correspond to
actual bugs or API drift.

### `agent/runner.py` — PydanticAI prompt list mismatch

`append` called with `UserPromptPart` on a list typed as
`list[SystemPromptPart | ToolReturnPart | RetryPromptPart]`. The part type
or the list annotation needs to be corrected.

### `world/repository.py` — `upsert_world_fact` signature drift

Two call sites pass `value_json=` and `confidence=` as keyword arguments but
the method signature does not declare them. Either the method was refactored
and call sites were not updated, or the method needs those parameters added.

### `scheduler/jobs.py` — APScheduler `IntervalTrigger` API change

`start_delay` is passed to `IntervalTrigger` but is not a valid kwarg. This
likely reflects an APScheduler version upgrade where the argument was renamed.
Check the installed version and update the call.

### `control/api.py` — `InboundEvent` argument types

Three arguments typed `str | datetime | None` are passed where `str` is
expected. Add explicit string guards before the call.

### Miscellaneous assignment errors

- `HouseholdMember | None` assigned to `HouseholdMember` without a None check.
- `timezone` assigned to a variable annotated as `ZoneInfo`.

Both need a guard or annotation correction.

---

## Phase 4 — Enable mypy in CI

Once `uv run mypy app/` exits with 0 errors, add it back to
`.github/workflows/ci.yml`:

```yaml
- name: Type check (mypy)
  run: uv run mypy app/
```

Place it after the format check and before the unit tests so the failure signal
is ordered from fastest to slowest check.

---

## Progress tracking

Run this after each phase to measure remaining errors:

```bash
uv run mypy app/ 2>&1 | grep "^Found"
```

Target: `Success: no issues found in 126 source files`
