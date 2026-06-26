# World Model Brief

Status: implemented core, ongoing refinements
Last code check: 2026-06-26

## Purpose

The world model is the canonical structured household layer for members, places,
devices, calendars, routines, relationships, and durable facts. It grounds the
agent more reliably than free-text memory.

## Main Files

- `app/models/world.py` - SQLModel world-model tables.
- `app/world/repository.py` - persistence and query helpers.
- `app/world/formatter.py` - compact prompt rendering.
- `app/world/sync.py` - startup/bootstrap sync.
- `app/world/extraction.py` - background extraction/update proposals.
- `app/agent/tools/world_model.py` - agent read/write tools.

## Invariants

- Canonical household structure belongs here, not in episodic memory.
- Respect user-asserted names and identity links.
- Use conservative updates; avoid overwriting trusted structured facts from weak
  conversational hints.
- Keep prompt formatting compact and predictable.

## Verification

- `uv run pytest tests/unit/test_formatter.py -v`
- `uv run pytest tests/integration/test_world_repository.py -v` when repository behavior changes.
- `uv run mypy app/world app/models` during typing work.

## Deeper Docs

- `docs/household-world-model-design.md`
- `docs/world-model-usage-improvements.md`
- `docs/memory-design.md`
- `docs/user-identity-memory-link-design.md`
