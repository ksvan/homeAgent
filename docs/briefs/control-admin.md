# Control Admin Brief

Status: implemented core admin surface, ongoing refinements
Last code check: 2026-06-26

## Purpose

The admin/control surface provides operational visibility over live events,
agent runs, tasks, scheduler state, email intake, world model data, skills, and
control-loop behavior.

## Main Files

- `app/control/api.py` - admin routes and API helpers.
- `app/control/dashboard.html` - browser UI.
- `app/control/events.py` - SSE ring buffer and subscribers.
- `app/control/event_bus.py` - internal event bus.
- `app/control/dispatcher.py` - event rule dispatch.
- `app/control/loop_service.py` - control task correlation.
- `app/control/internal_events.py` - verification/result event helpers.
- `app/__main__.py` - separate admin app wiring.

## Invariants

- Admin UI runs on the admin port, not the main webhook app.
- Prefer reusing existing event emissions and durable state over adding a second
  observability system.
- Be careful editing `dashboard.html`; it is file content, not a Python string.
- Keep expensive admin queries bounded.

## Verification

- `uv run pytest tests/unit/test_events.py -v`
- `uv run ruff check app/control`
- Browser/manual check when editing `dashboard.html`.

## Deeper Docs

- `docs/control-loop-admin-tab-design.md`
- `docs/autonomy-control-loop-design.md`
- `docs/observability.md`
