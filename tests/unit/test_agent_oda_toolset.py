"""Tests for app.agent.agent._make_conversation_agent's Oda MCP toolset
wiring — two-gate: the FEATURE_ODA flag AND an actually-connected account
(the latter already encoded in app.oda.mcp_client.get_mcp_server()
returning None when the household hasn't connected — see
docs/oda-grocery-mcp-tool-design.md "Feature flag").
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from pydantic_ai.models.test import TestModel

from app.agent.agent import _make_conversation_agent
from app.config import get_settings


@pytest.fixture(autouse=True)
def _isolated_settings() -> None:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _build_agent(*, oda_server: object) -> object:
    with (
        patch("app.agent.agent.LLMRouter.get_model", return_value=TestModel()),
        patch("app.homey.mcp_client.get_mcp_toolset", return_value=None),
        patch("app.prometheus.mcp_client.get_mcp_server", return_value=None),
        patch("app.tools.mcp_client.get_mcp_server", return_value=None),
        patch("app.oda.mcp_client.get_mcp_server", return_value=oda_server),
    ):
        return _make_conversation_agent()


def _toolset_count(*, oda_server: object) -> int:
    """pydantic_ai's Agent.toolsets includes its own internal
    function-toolset alongside whatever MCP toolsets were passed in, and
    wraps each in a DynamicToolset — so absolute counts/identity checks
    aren't meaningful. Comparing counts with/without the Oda server present
    is what actually isolates the effect of the two-gate logic."""
    return len(list(_build_agent(oda_server=oda_server).toolsets))  # type: ignore[attr-defined]


def test_oda_toolset_excluded_when_feature_flag_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FEATURE_ODA", "false")
    get_settings.cache_clear()

    baseline = _toolset_count(oda_server=None)
    # Even a "connected" server must be excluded when the flag is off.
    with_connected_server = _toolset_count(oda_server=object())
    assert with_connected_server == baseline


def test_oda_toolset_excluded_when_flag_enabled_but_not_connected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FEATURE_ODA", "true")
    get_settings.cache_clear()

    baseline = _toolset_count(oda_server=None)
    still_not_connected = _toolset_count(oda_server=None)
    assert still_not_connected == baseline


def test_oda_toolset_included_when_flag_enabled_and_connected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FEATURE_ODA", "true")
    get_settings.cache_clear()

    baseline = _toolset_count(oda_server=None)
    with_connected_server = _toolset_count(oda_server=object())
    assert with_connected_server == baseline + 1
