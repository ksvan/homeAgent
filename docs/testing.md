# Testing

## Structure

```
tests/
├── __init__.py
├── unit/                 # Fast, no external deps — runs in CI
│   ├── __init__.py
│   ├── test_config.py    # Settings validators, feature flags, path helpers
│   ├── test_events.py    # SSE ring buffer, subscriber queues, emit logic
│   └── test_formatter.py # World model formatter (all section builders)
└── integration/          # (future) Requires DB / MCP — not in CI yet
```

## Running Tests

```bash
# All unit tests (fast, no external deps)
uv run pytest tests/unit/ -v

# Single file
uv run pytest tests/unit/test_formatter.py -v

# With coverage
uv run pytest tests/unit/ --cov=app --cov-report=term-missing
```

## What's Tested

| Module | Test file | What's covered |
|--------|-----------|----------------|
| `app/config.py` | `test_config.py` | `parse_int_list` validator, webhook secret validator, environment helpers, `db_path`, feature flags |
| `app/control/events.py` | `test_events.py` | Ring buffer cap (150), emit + subscribe/unsubscribe, queue overflow safety, auto-generated run IDs |
| `app/world/formatter.py` | `test_formatter.py` | `_parse_aliases`, all `_add_*` section builders, `WorldModelSnapshot.is_empty`, hierarchy rendering |

## Adding Tests

1. Create `tests/unit/test_<module>.py`
2. Follow existing patterns — no fixtures needed for pure logic tests
3. For world model tests, use factory helpers like `_member()`, `_place()`, `_device()` (see `test_formatter.py`)
4. Tests that need a database belong in `tests/integration/` (not yet implemented)
5. Run `uv run ruff check tests/` before committing

## Good Candidates for Future Tests

- `app/policy/gate.py` — policy pattern matching, read-only detection, confirmation messages
- `app/scheduler/cleanup.py` — requires DB mocking or integration test setup
- `app/world/repository.py` — DB layer, better as integration tests

## CI Integration

Tests run automatically on every push and PR to `main` via GitHub Actions. See [ci.md](ci.md) for details.
