from __future__ import annotations

import logging
import sqlite3
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path
from typing import Generator

import sqlite_vec
from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlmodel import Session, create_engine

from app.config import get_settings

logger = logging.getLogger(__name__)

# Dimension of OpenAI text-embedding-3-small vectors
EMBEDDING_DIM = 1536

_vec_available: bool = False


def is_vec_available() -> bool:
    """Return True if sqlite-vec loaded successfully on the memory engine."""
    return _vec_available


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
        dbapi_conn.execute("PRAGMA busy_timeout=5000")
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    return engine


def _make_memory_engine() -> Engine:
    """Like _make_engine but also loads the sqlite-vec extension."""
    engine = _make_engine("memory")

    @event.listens_for(engine, "connect")
    def _load_vec(dbapi_conn: sqlite3.Connection, _: object) -> None:
        global _vec_available
        try:
            dbapi_conn.enable_load_extension(True)
            sqlite_vec.load(dbapi_conn)
            dbapi_conn.enable_load_extension(False)
            dbapi_conn.execute(
                f"CREATE VIRTUAL TABLE IF NOT EXISTS episodic_memory_vec "
                f"USING vec0(embedding float[{EMBEDDING_DIM}])"
            )
            _vec_available = True
        except Exception:
            _vec_available = False
            logger.warning(
                "sqlite-vec extension could not be loaded — vector search disabled",
                exc_info=True,
            )

    return engine


@lru_cache(maxsize=1)
def users_engine() -> Engine:
    return _make_engine("users")


@lru_cache(maxsize=1)
def memory_engine() -> Engine:
    return _make_memory_engine()


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
