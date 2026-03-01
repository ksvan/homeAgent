from __future__ import annotations

import logging
import sqlite3
import struct
from contextlib import contextmanager
from typing import Generator

from sqlmodel import col, select

from app.db import EMBEDDING_DIM, memory_engine, memory_session
from app.models.memory import EpisodicMemory

logger = logging.getLogger(__name__)

_MAX_RESULTS = 5


@contextmanager
def _raw_memory_conn() -> Generator[sqlite3.Connection, None, None]:
    """Yield the raw sqlite3 connection from the memory engine pool."""
    with memory_engine().connect() as conn:
        raw = conn.connection.driver_connection
        assert raw is not None, "driver_connection is None"
        yield raw


def _get_embedding(text: str) -> list[float] | None:
    """Return an embedding vector for *text* via the OpenAI API, or None on failure."""
    from app.config import get_settings

    settings = get_settings()
    if not settings.openai_api_key:
        return None

    try:
        import openai

        client = openai.OpenAI(api_key=settings.openai_api_key)
        response = client.embeddings.create(model=settings.model_embedding, input=text)
        return response.data[0].embedding
    except Exception:
        logger.warning("Embedding request failed — skipping vector indexing", exc_info=True)
        return None


def _pack_embedding(floats: list[float]) -> bytes:
    return struct.pack(f"{EMBEDDING_DIM}f", *floats)


def store_memory(
    household_id: str,
    content: str,
    user_id: str | None = None,
    source_run_id: str | None = None,
) -> str:
    """
    Persist an episodic memory.

    Generates an embedding (if possible) and stores it in the sqlite-vec
    virtual table.  Returns the EpisodicMemory.id of the new record.
    """
    embedding = _get_embedding(content)

    with memory_session() as session:
        record = EpisodicMemory(
            household_id=household_id,
            user_id=user_id,
            content=content,
            source_run_id=source_run_id,
        )
        session.add(record)
        session.commit()
        session.refresh(record)
        memory_id = record.id

    if embedding is not None:
        _insert_into_vec(memory_id, embedding)

    return memory_id


def _insert_into_vec(memory_id: str, embedding: list[float]) -> None:
    """Insert the embedding into the sqlite-vec virtual table."""
    try:
        vec_bytes = _pack_embedding(embedding)
        with _raw_memory_conn() as raw:
            raw.execute(
                "INSERT OR REPLACE INTO episodic_memory_vec(embedding) VALUES (?)",
                [vec_bytes],
            )
            rowid = raw.execute("SELECT last_insert_rowid()").fetchone()[0]
            raw.commit()

        # Store the rowid so we can look it up during search
        with memory_session() as session:
            record = session.exec(
                select(EpisodicMemory).where(EpisodicMemory.id == memory_id)
            ).first()
            if record:
                record.embedding_id = str(rowid)
                session.commit()
    except Exception:
        logger.warning("Failed to insert embedding into vec table", exc_info=True)


def search_memories(
    household_id: str,
    query: str,
    user_id: str,
    limit: int = _MAX_RESULTS,
) -> list[str]:
    """
    Return the *content* of the most relevant episodic memories for *query*.

    Scoped to memories visible to this user:
    - Household-wide memories (user_id IS NULL)
    - Personal memories belonging to this user only

    Personal memories of other household members are never returned.
    Falls back to recency-based retrieval when embeddings aren't available.
    """
    embedding = _get_embedding(query)

    if embedding is not None:
        texts = _vec_search(household_id, user_id, embedding, limit)
        if texts:
            return texts

    return _recency_fallback(household_id, user_id, limit)


def _visible_filter(household_id: str, user_id: str) -> object:
    """SQLAlchemy clause: household-wide OR personal memories for this user."""
    from sqlalchemy import and_, or_

    return and_(
        col(EpisodicMemory.household_id) == household_id,
        or_(
            col(EpisodicMemory.user_id).is_(None),
            col(EpisodicMemory.user_id) == user_id,
        ),
    )


def _vec_search(
    household_id: str, user_id: str, embedding: list[float], limit: int
) -> list[str]:
    try:
        vec_bytes = _pack_embedding(embedding)
        with _raw_memory_conn() as raw:
            rows = raw.execute(
                "SELECT rowid FROM episodic_memory_vec "
                "WHERE embedding MATCH ? ORDER BY distance LIMIT ?",
                [vec_bytes, limit * 3],  # over-fetch; filter by user below
            ).fetchall()

        if not rows:
            return []

        rowids = [str(r[0]) for r in rows]
        with memory_session() as session:
            memories = session.exec(
                select(EpisodicMemory)
                .where(_visible_filter(household_id, user_id))  # type: ignore[arg-type]
                .where(col(EpisodicMemory.embedding_id).in_(rowids))
                .limit(limit)
            ).all()
        return [m.content for m in memories]
    except Exception:
        logger.warning("Vec search failed — falling back to recency", exc_info=True)
        return []


def _recency_fallback(household_id: str, user_id: str, limit: int) -> list[str]:
    with memory_session() as session:
        memories = session.exec(
            select(EpisodicMemory)
            .where(_visible_filter(household_id, user_id))  # type: ignore[arg-type]
            .order_by(col(EpisodicMemory.created_at).desc())
            .limit(limit)
        ).all()
    return [m.content for m in memories]
