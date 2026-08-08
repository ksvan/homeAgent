"""Shared test infrastructure.

Provides an in-memory SQLite engine with all SQLModel tables created.
Tests that need DB access should use the `in_memory_engine` fixture and
monkeypatch the relevant session factory in their own module fixture.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Generator

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

# Import all model modules so their tables register with SQLModel.metadata
# before any fixture calls create_all().
import app.models.cache  # noqa: F401
import app.models.integrations  # noqa: F401
import app.models.memory  # noqa: F401
import app.models.scheduled_prompts  # noqa: F401
import app.models.tasks  # noqa: F401
import app.models.users  # noqa: F401
import app.models.world  # noqa: F401


@pytest.fixture
def in_memory_engine():
    """Fresh in-memory SQLite engine with all tables created per test.

    StaticPool pins the engine to a single underlying connection for its
    whole lifetime. Without it, sqlite:///:memory: hands out a distinct
    (empty) database per thread it's touched from — SQLAlchemy's default
    SingletonThreadPool behavior for in-memory SQLite — which is a flaky
    "no such table" trap the moment an async test's DB access happens on a
    different thread than create_all() ran on (e.g. via anyio/asyncio
    worker-thread scheduling, or a FastAPI TestClient's background thread).
    """
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    yield engine
    engine.dispose()


@contextmanager
def session_factory(engine: object) -> Generator[Session, None, object]:
    """Helper: context manager that yields a Session on the given engine."""
    with Session(engine) as s:  # type: ignore[arg-type]
        yield s
