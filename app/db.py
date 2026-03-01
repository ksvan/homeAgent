from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path
from typing import Generator

from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlmodel import Session, create_engine

from app.config import get_settings


def _make_engine(db_name: str) -> Engine:
    settings = get_settings()
    db_path = settings.db_path(db_name)
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def _enable_wal(dbapi_conn: sqlite3.Connection, _: object) -> None:
        dbapi_conn.execute("PRAGMA journal_mode=WAL")

    return engine


@lru_cache(maxsize=1)
def users_engine() -> Engine:
    return _make_engine("users")


@lru_cache(maxsize=1)
def memory_engine() -> Engine:
    return _make_engine("memory")


@lru_cache(maxsize=1)
def cache_engine() -> Engine:
    return _make_engine("cache")


@contextmanager
def users_session() -> Generator[Session, None, None]:
    with Session(users_engine()) as session:
        yield session


@contextmanager
def memory_session() -> Generator[Session, None, None]:
    with Session(memory_engine()) as session:
        yield session


@contextmanager
def cache_session() -> Generator[Session, None, None]:
    with Session(cache_engine()) as session:
        yield session
