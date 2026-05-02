from __future__ import annotations

from datetime import date, timedelta

from app.wine.models import WineBottle, WineSyncResult


def _bottle(**kwargs: object) -> WineBottle:
    defaults: dict[str, object] = {
        "id": "abc123",
        "shelf": None,
        "category": "Rødvin",
        "country": "Italia",
        "producer": "Conterno",
        "name": "Barolo",
        "vintage": 2018,
        "drink_window_end": None,
        "score": 95.0,
        "purchase_price_nok": 500.0,
        "region": "Barolo",
        "note": None,
        "consumed": False,
        "source_row": 2,
        "source_hash": "abc",
    }
    defaults.update(kwargs)
    return WineBottle(**defaults)  # type: ignore[arg-type]


def test_available():
    assert _bottle(consumed=False).available is True
    assert _bottle(consumed=True).available is False


def test_drink_status_unknown():
    assert _bottle(drink_window_end=None).drink_status == "unknown"


def test_drink_status_past_window():
    yesterday = date.today() - timedelta(days=1)
    assert _bottle(drink_window_end=yesterday).drink_status == "past_window"


def test_drink_status_drink_now():
    soon = date.today() + timedelta(days=100)
    assert _bottle(drink_window_end=soon).drink_status == "drink_now"


def test_drink_status_hold():
    far = date.today() + timedelta(days=1000)
    assert _bottle(drink_window_end=far).drink_status == "hold"


def test_display_name():
    b = _bottle(producer="Conterno", name="Barolo", vintage=2018)
    assert "Conterno" in b.display_name
    assert "Barolo" in b.display_name
    assert "2018" in b.display_name


def test_display_name_no_producer():
    b = _bottle(producer=None, name="Barolo", vintage=2015)
    assert b.display_name == "Barolo (2015)"


def test_sync_result_summary_success():
    from datetime import datetime, timezone

    r = WineSyncResult(
        success=True,
        row_count=42,
        etag="xyz",
        synced_at=datetime(2026, 4, 28, 6, 0, 0, tzinfo=timezone.utc),
    )
    summary = r.to_summary()
    assert "42 bottles" in summary
    assert "2026-04-28" in summary


def test_sync_result_summary_failure():
    r = WineSyncResult(success=False, error="Graph timeout")
    assert "Graph timeout" in r.to_summary()


def test_sync_result_summary_stale():
    from datetime import datetime, timezone

    r = WineSyncResult(
        success=True,
        row_count=10,
        stale=True,
        synced_at=datetime(2026, 4, 27, 6, 0, 0, tzinfo=timezone.utc),
    )
    summary = r.to_summary()
    assert "cached" in summary
