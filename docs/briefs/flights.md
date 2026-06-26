# Flights Brief

Status: partially implemented, active runtime
Last code check: 2026-06-26

## Purpose

Flight tracking lets HomeAgent watch flights, fetch status from a provider,
detect meaningful changes, and notify the user when timing, gate, boarding, or
status changes matter.

## Main Files

- `app/flights/models.py` - domain models and normalized statuses.
- `app/models/flights.py` - durable flight watch/cache tables.
- `app/flights/providers/base.py` - provider interface.
- `app/flights/providers/aerodatabox.py` - AeroDataBox provider.
- `app/flights/repository.py` - flight persistence.
- `app/flights/service.py` - tracking and status lookup service.
- `app/flights/scheduler.py` - polling/subscription jobs.
- `app/flights/diff.py` - deterministic change detection.
- `app/flights/notifications.py` - user notification policy.
- `app/agent/tools/flights.py` - agent flight tools.

## Invariants

- Normalize provider-specific states before comparing or notifying.
- Use deterministic filtering before involving the agent.
- Keep webhook ingestion and polling fallback consistent.
- Avoid duplicate or stale notifications across quiet-hours filters.

## Verification

- Run focused flight tests when present.
- `uv run ruff check app/flights app/agent/tools/flights.py`
- `uv run pytest tests/unit/ -v --tb=short`

## Deeper Docs

- `docs/flight-monitor-agent-design.md`
- `docs/architecture.md`
