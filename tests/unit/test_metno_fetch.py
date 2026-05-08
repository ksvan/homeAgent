from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path
from types import ModuleType
from urllib.parse import parse_qs, urlparse


def load_metno_fetch() -> ModuleType:
    path = (
        Path(__file__).parents[2]
        / "app"
        / "skills"
        / "metno-norway-weather"
        / "scripts"
        / "metno_fetch.py"
    )
    spec = importlib.util.spec_from_file_location("metno_fetch", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_geocode_url_uses_kartverket_address_api() -> None:
    metno_fetch = load_metno_fetch()

    url = metno_fetch.encode_geocode_url("Kvernfaret 28, 0383 Oslo", limit=1)

    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    assert parsed.scheme == "https"
    assert parsed.netloc == "ws.geonorge.no"
    assert parsed.path == "/adresser/v1/sok"
    assert query["sok"] == ["Kvernfaret 28, 0383 Oslo"]
    assert query["treffPerSide"] == ["1"]
    assert "adresser.representasjonspunkt" in query["filtrer"][0]


def test_locationforecast_accepts_address_by_resolving_coordinates(monkeypatch) -> None:
    metno_fetch = load_metno_fetch()

    def fake_resolve_address(address: str, timeout: float, user_agent: str) -> dict[str, object]:
        assert address == "Kvernfaret 28, 0383 Oslo"
        assert timeout == 20.0
        assert user_agent == "homeAgent metno-norway-weather"
        return {"representasjonspunkt": {"lat": 59.929120245243936, "lon": 10.630627769281588}}

    monkeypatch.setattr(metno_fetch, "resolve_address", fake_resolve_address)
    args = argparse.Namespace(
        lat=None,
        lon=None,
        address="Kvernfaret 28, 0383 Oslo",
        altitude=None,
        mode="compact",
        timeout=20.0,
        user_agent=None,
    )

    url = metno_fetch.build_locationforecast(args)

    assert url == "https://api.met.no/weatherapi/locationforecast/2.0/compact?lat=59.9291&lon=10.6306"


def test_locationforecast_rejects_mixed_address_and_coordinates() -> None:
    metno_fetch = load_metno_fetch()
    args = argparse.Namespace(
        lat=59.9,
        lon=10.6,
        address="Kvernfaret 28, 0383 Oslo",
        altitude=None,
        mode="compact",
        timeout=20.0,
        user_agent=None,
    )

    try:
        metno_fetch.build_locationforecast(args)
    except SystemExit as exc:
        assert "Use either --address or both --lat and --lon" in str(exc)
    else:
        raise AssertionError("Expected mixed address and coordinates to fail")
