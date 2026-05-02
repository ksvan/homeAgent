from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def get_sync_meta() -> object:
    """Return the WineSyncMeta row, or None if the table is empty."""
    from app.db import cache_session
    from app.models.wine import WineSyncMeta

    with cache_session() as session:
        return session.get(WineSyncMeta, "default")


def upsert_snapshot(
    bottles: list[object],
    etag: str,
    warnings: list[str],
    error: str | None = None,
) -> None:
    """Atomically replace all wine bottle rows and update sync metadata."""
    from sqlmodel import delete

    from app.db import cache_session
    from app.models.wine import WineBottleRow, WineSyncMeta
    from app.wine.models import WineBottle

    now = datetime.now(timezone.utc)

    with cache_session() as session:
        # Delete all existing bottle rows
        session.exec(delete(WineBottleRow))  # type: ignore[call-overload]

        # Insert new rows
        for b in bottles:
            assert isinstance(b, WineBottle)
            row = WineBottleRow(
                id=b.id,
                shelf=b.shelf,
                category=b.category,
                country=b.country,
                producer=b.producer,
                name=b.name,
                vintage=b.vintage,
                drink_window_end=b.drink_window_end,
                score=b.score,
                purchase_price_nok=b.purchase_price_nok,
                region=b.region,
                note=b.note,
                consumed=b.consumed,
                source_row=b.source_row,
                source_hash=b.source_hash,
                synced_at=now,
            )
            session.add(row)

        # Upsert sync metadata
        meta = session.get(WineSyncMeta, "default")
        if meta is None:
            meta = WineSyncMeta(id="default")
        meta.etag = etag
        meta.last_sync_at = now
        meta.last_attempt_at = now
        meta.row_count = len(bottles)
        meta.parse_warnings = json.dumps(warnings)
        meta.sync_error = error
        session.add(meta)
        session.commit()


def record_sync_attempt(error: str | None = None) -> None:
    """Update last_attempt_at and sync_error without replacing bottle rows."""
    from app.db import cache_session
    from app.models.wine import WineSyncMeta

    now = datetime.now(timezone.utc)
    with cache_session() as session:
        meta = session.get(WineSyncMeta, "default")
        if meta is None:
            meta = WineSyncMeta(id="default")
        meta.last_attempt_at = now
        meta.sync_error = error
        session.add(meta)
        session.commit()


def get_all_bottles() -> list[object]:
    """Return all persisted WineBottle instances from cache.db."""
    from sqlmodel import select

    from app.db import cache_session
    from app.models.wine import WineBottleRow
    from app.wine.models import WineBottle

    with cache_session() as session:
        rows = session.exec(select(WineBottleRow)).all()

    return [
        WineBottle(
            id=r.id,
            shelf=r.shelf,
            category=r.category,
            country=r.country,
            producer=r.producer,
            name=r.name,
            vintage=r.vintage,
            drink_window_end=r.drink_window_end,
            score=r.score,
            purchase_price_nok=r.purchase_price_nok,
            region=r.region,
            note=r.note,
            consumed=r.consumed,
            source_row=r.source_row,
            source_hash=r.source_hash,
        )
        for r in rows
    ]


def get_bottle_by_id(bottle_id: str) -> object | None:
    """Return a single WineBottle by id, or None."""
    from app.db import cache_session
    from app.models.wine import WineBottleRow
    from app.wine.models import WineBottle

    with cache_session() as session:
        r = session.get(WineBottleRow, bottle_id)
        if r is None:
            return None
        return WineBottle(
            id=r.id,
            shelf=r.shelf,
            category=r.category,
            country=r.country,
            producer=r.producer,
            name=r.name,
            vintage=r.vintage,
            drink_window_end=r.drink_window_end,
            score=r.score,
            purchase_price_nok=r.purchase_price_nok,
            region=r.region,
            note=r.note,
            consumed=r.consumed,
            source_row=r.source_row,
            source_hash=r.source_hash,
        )
