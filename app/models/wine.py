from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Optional

from sqlmodel import Field, SQLModel


def _now() -> datetime:
    return datetime.now(timezone.utc)


class WineBottleRow(SQLModel, table=True):
    """Persisted snapshot of a single bottle parsed from the Excel workbook."""

    id: str = Field(primary_key=True)
    shelf: Optional[str] = None
    category: Optional[str] = None
    country: Optional[str] = None
    producer: Optional[str] = None
    name: str
    vintage: Optional[int] = None
    drink_window_end: Optional[date] = None
    score: Optional[float] = None
    purchase_price_nok: Optional[float] = None
    region: Optional[str] = None
    note: Optional[str] = None
    consumed: bool = False
    source_row: int = 0
    source_hash: str = ""
    synced_at: datetime = Field(default_factory=_now)


class WineSyncMeta(SQLModel, table=True):
    """Single-row sync metadata for the wine workbook."""

    id: str = Field(default="default", primary_key=True)
    etag: str = ""
    last_sync_at: Optional[datetime] = None
    last_attempt_at: Optional[datetime] = None
    row_count: int = 0
    parse_warnings: str = "[]"  # JSON array of warning strings
    sync_error: Optional[str] = None
