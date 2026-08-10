from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest


def _load_probe_module() -> ModuleType:
    path = (
        Path(__file__).parents[2]
        / "app"
        / "skills"
        / "unifi-prometheus-network"
        / "scripts"
        / "network_probe.py"
    )
    spec = importlib.util.spec_from_file_location("network_probe", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_network_probe_rejects_unsafe_or_invalid_input() -> None:
    probe = _load_probe_module()

    with pytest.raises(ValueError, match="credentialed"):
        probe.parse_http_url("https://user:secret@example.test/")
    with pytest.raises(ValueError, match="absolute"):
        probe.parse_http_url("ftp://example.test/")
    with pytest.raises(ValueError, match="timeout"):
        probe.parse_timeout(11)


def test_network_probe_reports_invalid_port_as_json(capsys: pytest.CaptureFixture[str]) -> None:
    probe = _load_probe_module()

    assert probe.main(["tcp", "--host", "example.test", "--port", "0"]) == 0

    output = capsys.readouterr().out
    assert '"error_type": "input"' in output
    assert "port must be between 1 and 65535" in output
