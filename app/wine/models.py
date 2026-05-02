from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime


@dataclass
class WineBottle:
    id: str
    shelf: str | None
    category: str | None
    country: str | None
    producer: str | None
    name: str
    vintage: int | None
    drink_window_end: date | None
    score: float | None
    purchase_price_nok: float | None
    region: str | None
    note: str | None
    consumed: bool
    source_row: int
    source_hash: str

    @property
    def available(self) -> bool:
        return not self.consumed

    @property
    def drink_status(self) -> str:
        if self.drink_window_end is None:
            return "unknown"
        from datetime import timedelta

        today = date.today()
        if self.drink_window_end < today:
            return "past_window"
        if self.drink_window_end <= today + timedelta(days=365):
            return "drink_now"
        return "hold"

    @property
    def display_name(self) -> str:
        parts = [p for p in [self.producer, self.name] if p]
        result = " — ".join(parts) if parts else self.name
        if self.vintage:
            result += f" ({self.vintage})"
        return result


@dataclass
class WineSyncResult:
    success: bool
    row_count: int = 0
    etag: str = ""
    synced_at: datetime | None = None
    stale: bool = False
    parse_warnings: list[str] = field(default_factory=list)
    error: str | None = None

    def to_summary(self) -> str:
        if not self.success and self.error:
            return f"Sync failed: {self.error}"
        parts = [f"{self.row_count} bottles loaded"]
        if self.stale and self.synced_at:
            ts = self.synced_at.strftime("%Y-%m-%d %H:%M UTC")
            parts.append(f"cached snapshot from {ts}")
        elif self.synced_at:
            ts = self.synced_at.strftime("%Y-%m-%d %H:%M UTC")
            parts.append(f"synced {ts}")
        if self.parse_warnings:
            parts.append(f"{len(self.parse_warnings)} parse warning(s)")
        return "; ".join(parts)


def make_bottle_id(source_row: int, etag: str) -> str:
    import hashlib

    return hashlib.sha1(f"{etag}:{source_row}".encode()).hexdigest()[:16]
