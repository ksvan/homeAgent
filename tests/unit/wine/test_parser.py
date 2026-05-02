from __future__ import annotations

from datetime import date
from io import BytesIO

import openpyxl
import pytest


def _make_xlsx(rows: list[list[object]], headers: list[str] | None = None) -> bytes:
    """Build a minimal xlsx workbook in memory."""
    wb = openpyxl.Workbook()
    ws = wb.active
    if headers:
        ws.append(headers)
    for row in rows:
        ws.append(row)
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


ETAG = "test-etag-abc"


def test_parse_basic_row():
    content = _make_xlsx(
        headers=["Vin", "Vinprodusent", "Land", "Kategori", "Årgang", "Drukket"],
        rows=[["Barolo", "Giacomo Conterno", "Italia", "Rødvin", 2018, "Nei"]],
    )
    from app.wine.parser import parse_xlsx, rows_to_bottles

    rows, warnings = parse_xlsx(content, ETAG)
    assert len(rows) == 1
    assert not warnings
    bottles = rows_to_bottles(rows)
    b = bottles[0]
    assert b.name == "Barolo"
    assert b.producer == "Giacomo Conterno"
    assert b.country == "Italia"
    assert b.category == "Rødvin"
    assert b.vintage == 2018
    assert b.consumed is False
    assert b.available is True


def test_consumed_ja():
    content = _make_xlsx(
        headers=["Vin", "Drukket"],
        rows=[["Chablis", "Ja"]],
    )
    from app.wine.parser import parse_xlsx, rows_to_bottles

    rows, _ = parse_xlsx(content, ETAG)
    bottles = rows_to_bottles(rows)
    assert bottles[0].consumed is True
    assert bottles[0].available is False


def test_consumed_empty_means_available():
    content = _make_xlsx(
        headers=["Vin", "Drukket"],
        rows=[["Chablis", None]],
    )
    from app.wine.parser import parse_xlsx, rows_to_bottles

    rows, _ = parse_xlsx(content, ETAG)
    bottles = rows_to_bottles(rows)
    assert bottles[0].consumed is False


def test_column_aliases():
    content = _make_xlsx(
        headers=["Vin", "Produsent", "Aargang"],
        rows=[["Rioja", "Lopez de Heredia", 2015]],
    )
    from app.wine.parser import parse_xlsx, rows_to_bottles

    rows, warnings = parse_xlsx(content, ETAG)
    # Only preferred-column warnings expected (minimal test sheet)
    assert not any("Unknown" in w for w in warnings)
    bottles = rows_to_bottles(rows)
    assert bottles[0].producer == "Lopez de Heredia"
    assert bottles[0].vintage == 2015


def test_unknown_columns_warned():
    content = _make_xlsx(
        headers=["Vin", "UnknownColumn"],
        rows=[["Champagne", "foo"]],
    )
    from app.wine.parser import parse_xlsx

    _, warnings = parse_xlsx(content, ETAG)
    assert any("UnknownColumn" in w for w in warnings)


def test_skips_rows_without_name():
    content = _make_xlsx(
        headers=["Vin", "Land"],
        rows=[[None, "Italia"], ["Barolo", "Italia"]],
    )
    from app.wine.parser import parse_xlsx, rows_to_bottles

    rows, warnings = parse_xlsx(content, ETAG)
    bottles = rows_to_bottles(rows)
    assert len(bottles) == 1
    assert bottles[0].name == "Barolo"
    assert any("skipped" in w for w in warnings)


def test_drink_window_end_parsed():
    content = _make_xlsx(
        headers=["Vin", "Slutt drikkevindu"],
        rows=[["Sauternes", "2030-12-31"]],
    )
    from app.wine.parser import parse_xlsx, rows_to_bottles

    rows, _ = parse_xlsx(content, ETAG)
    bottles = rows_to_bottles(rows)
    assert bottles[0].drink_window_end == date(2030, 12, 31)


def test_drink_status_drink_now(monkeypatch):
    from datetime import timedelta

    import app.wine.models as wm

    today = date.today()
    soon = today + timedelta(days=180)
    content = _make_xlsx(
        headers=["Vin", "Slutt drikkevindu"],
        rows=[["Barolo", str(soon)]],
    )
    from app.wine.parser import parse_xlsx, rows_to_bottles

    rows, _ = parse_xlsx(content, ETAG)
    bottles = rows_to_bottles(rows)
    assert bottles[0].drink_status == "drink_now"


def test_drink_status_hold():
    from datetime import timedelta

    today = date.today()
    future = today + timedelta(days=1000)
    content = _make_xlsx(
        headers=["Vin", "Slutt drikkevindu"],
        rows=[["Barolo", str(future)]],
    )
    from app.wine.parser import parse_xlsx, rows_to_bottles

    rows, _ = parse_xlsx(content, ETAG)
    bottles = rows_to_bottles(rows)
    assert bottles[0].drink_status == "hold"


def test_missing_required_column_raises():
    content = _make_xlsx(
        headers=["Land", "Kategori"],
        rows=[["Italia", "Rødvin"]],
    )
    from app.wine.parser import parse_xlsx

    with pytest.raises(ValueError, match="Vin"):
        parse_xlsx(content, ETAG)


def test_price_coerced():
    content = _make_xlsx(
        headers=["Vin", "Pris innkjøp"],
        rows=[["Rioja", "299"]],
    )
    from app.wine.parser import parse_xlsx, rows_to_bottles

    rows, _ = parse_xlsx(content, ETAG)
    bottles = rows_to_bottles(rows)
    assert bottles[0].purchase_price_nok == 299.0


def test_stable_id_based_on_etag_and_row():
    content = _make_xlsx(
        headers=["Vin"],
        rows=[["Barolo"]],
    )
    from app.wine.parser import parse_xlsx, rows_to_bottles

    rows1, _ = parse_xlsx(content, "etag-v1")
    rows2, _ = parse_xlsx(content, "etag-v2")
    b1 = rows_to_bottles(rows1)[0]
    b2 = rows_to_bottles(rows2)[0]
    # Same row but different etag → different ID (snapshot changed)
    assert b1.id != b2.id
