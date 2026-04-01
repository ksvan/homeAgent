from __future__ import annotations

import logging
import sqlite3
import struct
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Generator

from sqlmodel import col, select

from app.db import EMBEDDING_DIM, is_vec_available, memory_engine, memory_session
from app.models.memory import EpisodicMemory

logger = logging.getLogger(__name__)

_MAX_RESULTS = 5

# Circuit breaker for the embedding API
_embedding_failures: int = 0
_embedding_open_until: float = 0.0
_CIRCUIT_OPEN_AFTER = 3
_CIRCUIT_COOLDOWN_SECS = 60


def _now() -> datetime:
    return datetime.now(timezone.utc)


@contextmanager
def _raw_memory_conn() -> Generator[sqlite3.Connection, None, None]:
    """Yield the raw sqlite3 connection from the memory engine pool."""
    with memory_engine().connect() as conn:
        raw = conn.connection.driver_connection
        assert raw is not None, "driver_connection is None"
        yield raw


def _get_embedding(text: str) -> list[float] | None:
    """Return an embedding vector for *text* via the OpenAI API, or None on failure.

    This is a synchronous call — use ``_get_embedding_async`` from async code
    to avoid blocking the event loop.
    """
    import time

    global _embedding_failures, _embedding_open_until

    from app.config import get_settings

    settings = get_settings()
    embedding_key = settings.model_embedding_api_key or settings.openai_api_key
    if not embedding_key:
        return None

    if time.monotonic() < _embedding_open_until:
        return None  # circuit open — skip HTTP call

    try:
        import openai

        client = openai.OpenAI(api_key=embedding_key)
        response = client.embeddings.create(model=settings.model_embedding, input=text)
        _embedding_failures = 0  # reset on success
        return response.data[0].embedding
    except Exception:
        _embedding_failures += 1
        if _embedding_failures >= _CIRCUIT_OPEN_AFTER:
            _embedding_open_until = time.monotonic() + _CIRCUIT_COOLDOWN_SECS
            logger.warning(
                "Embedding API circuit opened after %d failures — skipping for %ds",
                _embedding_failures,
                _CIRCUIT_COOLDOWN_SECS,
            )
        else:
            logger.warning("Embedding request failed — skipping vector indexing", exc_info=True)
        return None


async def _get_embedding_async(text: str) -> list[float] | None:
    """Non-blocking wrapper around ``_get_embedding`` for async callers."""
    import asyncio

    return await asyncio.to_thread(_get_embedding, text)


def _pack_embedding(floats: list[float]) -> bytes:
    return struct.pack(f"{EMBEDDING_DIM}f", *floats)


def _delete_from_vec(embedding_id: str | None) -> None:
    """Remove a row from the sqlite-vec virtual table. No-op if embedding_id is None."""
    if embedding_id is None:
        return
    try:
        with _raw_memory_conn() as raw:
            raw.execute(
                "DELETE FROM episodic_memory_vec WHERE rowid = ?",
                [int(embedding_id)],
            )
            raw.commit()
    except Exception:
        logger.warning("Failed to delete vec entry rowid=%s", embedding_id, exc_info=True)


def _find_duplicate(
    household_id: str,
    user_id: str | None,
    embedding: list[float],
) -> EpisodicMemory | None:
    """
    Return an existing memory whose embedding is within the dedup distance threshold,
    scoped to the same household + user_id visibility. Returns None if not found.
    """
    from app.config import get_settings

    threshold = get_settings().memory_dedup_distance_threshold
    try:
        vec_bytes = _pack_embedding(embedding)
        with _raw_memory_conn() as raw:
            rows = raw.execute(
                "SELECT rowid, distance FROM episodic_memory_vec "
                "WHERE embedding MATCH ? AND k = 1 ORDER BY distance",
                [vec_bytes],
            ).fetchall()

        if not rows:
            return None

        rowid, distance = rows[0]
        if distance >= threshold:
            return None

        with memory_session() as session:
            scope_filter = (
                col(EpisodicMemory.user_id).is_(None)
                if user_id is None
                else col(EpisodicMemory.user_id) == user_id
            )
            return session.exec(
                select(EpisodicMemory).where(
                    col(EpisodicMemory.embedding_id) == str(rowid),
                    col(EpisodicMemory.household_id) == household_id,
                    scope_filter,
                )
            ).first()
    except Exception:
        logger.warning("Dedup check failed — proceeding with insert", exc_info=True)
        return None


def store_memory(
    household_id: str,
    content: str,
    user_id: str | None = None,
    source_run_id: str | None = None,
    importance: str = "normal",
) -> str:
    """
    Persist an episodic memory.

    Checks for a near-duplicate first (vector similarity); if one exists in the
    same scope, touches its last_used_at and returns its ID without inserting.

    Otherwise generates an embedding (if possible) and stores the new record in
    both the SQLite table and the sqlite-vec virtual table.

    Returns the EpisodicMemory.id of the stored (or existing) record,
    or an empty string if the content was rejected by the PII guard.
    """
    from app.memory.pii import contains_pii

    if contains_pii(content):
        return ""

    embedding = _get_embedding(content) if is_vec_available() else None

    # Dedup: skip insert if a sufficiently similar memory already exists in scope
    if embedding is not None:
        duplicate = _find_duplicate(household_id, user_id, embedding)
        if duplicate is not None:
            with memory_session() as session:
                record = session.exec(
                    select(EpisodicMemory).where(EpisodicMemory.id == duplicate.id)
                ).first()
                if record:
                    record.last_used_at = _now()
                    session.add(record)
                    session.commit()
            logger.debug(
                "Dedup: skipped near-duplicate memory, refreshed id=%s", duplicate.id[:8]
            )
            return duplicate.id

    with memory_session() as session:
        record = EpisodicMemory(
            household_id=household_id,
            user_id=user_id,
            content=content,
            source_run_id=source_run_id,
            importance=importance,
        )
        session.add(record)
        session.commit()
        session.refresh(record)
        memory_id = record.id

    if embedding is not None:
        _insert_into_vec(memory_id, embedding)

    return memory_id


async def async_store_memory(
    household_id: str,
    content: str,
    user_id: str | None = None,
    source_run_id: str | None = None,
    importance: str = "normal",
) -> str:
    """Async variant of ``store_memory`` — offloads the embedding HTTP call
    to a thread so it doesn't block the event loop.

    Use this from async callers (background extraction, agent tools).
    """
    import asyncio

    from app.memory.pii import contains_pii

    if contains_pii(content):
        return ""

    embedding = (await asyncio.to_thread(_get_embedding, content)) if is_vec_available() else None

    # Dedup: skip insert if a sufficiently similar memory already exists in scope
    if embedding is not None:
        duplicate = _find_duplicate(household_id, user_id, embedding)
        if duplicate is not None:
            with memory_session() as session:
                record = session.exec(
                    select(EpisodicMemory).where(EpisodicMemory.id == duplicate.id)
                ).first()
                if record:
                    record.last_used_at = _now()
                    session.add(record)
                    session.commit()
            logger.debug(
                "Dedup: skipped near-duplicate memory, refreshed id=%s", duplicate.id[:8]
            )
            return duplicate.id

    with memory_session() as session:
        record = EpisodicMemory(
            household_id=household_id,
            user_id=user_id,
            content=content,
            source_run_id=source_run_id,
            importance=importance,
        )
        session.add(record)
        session.commit()
        session.refresh(record)
        memory_id = record.id

    if embedding is not None:
        _insert_into_vec(memory_id, embedding)

    return memory_id


def _insert_into_vec(memory_id: str, embedding: list[float]) -> None:
    """Insert the embedding into the sqlite-vec virtual table.

    Note: if this fails after the EpisodicMemory row has already been committed,
    the record will have embedding_id=NULL and degrade gracefully to recency-based
    retrieval — it will never appear in vec similarity searches but is otherwise
    fully functional.
    """
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
    Updates last_used_at on retrieved memories.
    Falls back to recency-based retrieval when embeddings aren't available.
    """
    if is_vec_available():
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
                "WHERE embedding MATCH ? AND k = ? ORDER BY distance",
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

            now = _now()
            for m in memories:
                m.last_used_at = now
                session.add(m)
            session.commit()

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

        now = _now()
        for m in memories:
            m.last_used_at = now
            session.add(m)
        session.commit()

        return [m.content for m in memories]
