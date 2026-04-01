# CI Pipeline (GitHub Actions)

## Overview

The CI pipeline runs on every push and pull request to `main`. It catches lint errors and test failures before merge.

**Config**: `.github/workflows/ci.yml`

## What Runs

| Step | Command | What it checks |
|------|---------|----------------|
| Lint | `uv run ruff check app/` | Code style, unused imports, line length (100 char) |
| Unit tests | `uv run pytest tests/unit/ -v --tb=short` | All tests in `tests/unit/` |

## Environment

- **Runner**: `ubuntu-latest`
- **Python**: 3.12 (installed via `uv python install`)
- **Package manager**: [uv](https://github.com/astral-sh/uv) with `--group dev` dependencies
- **No external services needed** — unit tests are self-contained

## What Blocks Merge

- Any ruff lint error in `app/`
- Any failing test in `tests/unit/`

## Running Locally

```bash
# Same checks as CI
uv run ruff check app/
uv run pytest tests/unit/ -v --tb=short

# Auto-fix lint issues
uv run ruff check app/ --fix
```

## Adding to CI

To add new checks (e.g., `mypy`), edit `.github/workflows/ci.yml` and add a step:

```yaml
- name: Type check (mypy)
  run: uv run mypy app/
```

## Notes

- Lint currently scopes to `app/` only (not `tests/` or `services/`)
- The test suite uses no mocking of external services — only pure logic is tested
- `[skip ci]` in a commit message skips the pipeline (not recommended)
