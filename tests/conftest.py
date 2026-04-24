"""Shared test infrastructure.

Provides an in-memory SQLite engine with all SQLModel tables created.
Tests that need DB access should use the `in_memory_engine` fixture and
monkeypatch the relevant session factory in their own module fixture.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Generator

import pytest
from sqlmodel import Session, SQLModel, create_engine

# Import all model modules so their tables register with SQLModel.metadata
# before any fixture calls create_all().
import app.models.memory  # noqa: F401
import app.models.tasks  # noqa: F401
import app.models.users  # noqa: F401
import app.models.world  # noqa: F401


@pytest.fixture
def in_memory_engine():
    """Fresh in-memory SQLite engine with all tables created per test."""
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    yield engine
    engine.dispose()


@contextmanager
def session_factory(engine: object) -> Generator[Session, None, object]:
    """Helper: context manager that yields a Session on the given engine."""
    with Session(engine) as s:  # type: ignore[arg-type]
        yield s
