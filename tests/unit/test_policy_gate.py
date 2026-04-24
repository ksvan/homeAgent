"""Unit tests for app.policy.gate.evaluate_policy().

Tests the tool-execution safety gate: pattern matching, arg conditions,
read-only defaults, DB failure fallback, and use_tool message building.
All DB calls are replaced by a monkeypatched session yielding in-memory policy objects.
"""
from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import MagicMock

import pytest

from app.policy.gate import PolicyDecision, _build_confirm_message, evaluate_policy


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _policy(**kwargs: object) -> MagicMock:
    """Build a minimal ActionPolicy-like mock."""
    p = MagicMock()
    p.name = kwargs.get("name", "test-policy")
    p.tool_pattern = kwargs.get("tool_pattern", "*")
    p.arg_conditions = kwargs.get("arg_conditions", "{}")
    p.impact_level = kwargs.get("impact_level", "medium")
    p.requires_confirm = kwargs.get("requires_confirm", True)
    p.confirm_message = kwargs.get("confirm_message", "")
    p.enabled = True
    return p


def _patch_session(monkeypatch: pytest.MonkeyPatch, policies: list[object]) -> None:
    """Replace users_session in gate module with one that returns the given policies."""

    @contextmanager
    def _fake_session():  # type: ignore[misc]
        session = MagicMock()
        session.exec.return_value.all.return_value = policies
        yield session

    monkeypatch.setattr("app.policy.gate.users_session", _fake_session)


def _patch_session_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace users_session with one that raises on exec."""

    @contextmanager
    def _failing_session():  # type: ignore[misc]
        session = MagicMock()
        session.exec.side_effect = RuntimeError("db down")
        yield session

    monkeypatch.setattr("app.policy.gate.users_session", _failing_session)


# ---------------------------------------------------------------------------
# Empty tool name
# ---------------------------------------------------------------------------


def test_empty_tool_name_returns_default(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_session(monkeypatch, [])
    result = evaluate_policy("", {})
    assert result == PolicyDecision()


# ---------------------------------------------------------------------------
# No matching policy — read-only vs write defaults
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("tool_name", ["get_device_state", "list_devices", "search_members"])
def test_read_only_tool_no_policy_allows(monkeypatch: pytest.MonkeyPatch, tool_name: str) -> None:
    _patch_session(monkeypatch, [])
    result = evaluate_policy(tool_name, {})
    assert result.requires_confirm is False
    assert result.policy_name == ""


@pytest.mark.parametrize(
    "tool_name", ["set_device_capability", "lock_door", "trigger_alarm", "use_tool"]
)
def test_write_tool_no_policy_requires_confirm(
    monkeypatch: pytest.MonkeyPatch, tool_name: str
) -> None:
    _patch_session(monkeypatch, [])
    result = evaluate_policy(tool_name, {})
    assert result.requires_confirm is True
    assert result.policy_name == "<unmatched>"


# ---------------------------------------------------------------------------
# Policy pattern matching
# ---------------------------------------------------------------------------


def test_exact_tool_name_match_applies_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    pol = _policy(
        tool_pattern="set_device_capability",
        requires_confirm=False,
        impact_level="low",
        name="Allow set cap",
    )
    _patch_session(monkeypatch, [pol])
    result = evaluate_policy("set_device_capability", {})
    assert result.requires_confirm is False
    assert result.policy_name == "Allow set cap"
    assert result.impact_level == "low"


def test_glob_pattern_matches(monkeypatch: pytest.MonkeyPatch) -> None:
    pol = _policy(tool_pattern="homey_*", requires_confirm=False, name="homey-all")
    _patch_session(monkeypatch, [pol])
    result = evaluate_policy("homey_set_light", {})
    assert result.requires_confirm is False
    assert result.policy_name == "homey-all"


def test_glob_pattern_miss_falls_through(monkeypatch: pytest.MonkeyPatch) -> None:
    pol = _policy(tool_pattern="homey_*", requires_confirm=False, name="homey-all")
    _patch_session(monkeypatch, [pol])
    # "set_light" does not start with "homey_"
    result = evaluate_policy("set_light", {})
    # No match → write tool default
    assert result.requires_confirm is True
    assert result.policy_name == "<unmatched>"


# ---------------------------------------------------------------------------
# arg_conditions
# ---------------------------------------------------------------------------


def test_arg_conditions_match_applies_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    pol = _policy(
        tool_pattern="set_*",
        arg_conditions='{"capability": "dim"}',
        requires_confirm=False,
        name="dimmer-allow",
    )
    _patch_session(monkeypatch, [pol])
    result = evaluate_policy("set_device_capability", {"capability": "dim", "value": 80})
    assert result.requires_confirm is False
    assert result.policy_name == "dimmer-allow"


def test_arg_conditions_miss_skips_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    pol = _policy(
        tool_pattern="set_*",
        arg_conditions='{"capability": "dim"}',
        requires_confirm=False,
        name="dimmer-allow",
    )
    _patch_session(monkeypatch, [pol])
    # capability is "alarm" — condition doesn't match → policy skipped
    result = evaluate_policy("set_device_capability", {"capability": "alarm"})
    assert result.requires_confirm is True
    assert result.policy_name == "<unmatched>"


def test_arg_conditions_glob_pattern(monkeypatch: pytest.MonkeyPatch) -> None:
    pol = _policy(
        tool_pattern="set_*",
        arg_conditions='{"capability": "dim*"}',
        requires_confirm=False,
        name="dim-glob",
    )
    _patch_session(monkeypatch, [pol])
    result = evaluate_policy("set_device_capability", {"capability": "dimmer"})
    assert result.requires_confirm is False


def test_malformed_arg_conditions_skips_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    pol = _policy(
        tool_pattern="set_*",
        arg_conditions="NOT_VALID_JSON",
        requires_confirm=False,
        name="broken",
    )
    _patch_session(monkeypatch, [pol])
    # Malformed JSON → policy skipped → write tool → confirm required
    result = evaluate_policy("set_device_capability", {})
    assert result.requires_confirm is True


def test_empty_arg_conditions_dict_matches_all(monkeypatch: pytest.MonkeyPatch) -> None:
    pol = _policy(
        tool_pattern="set_*",
        arg_conditions="{}",
        requires_confirm=False,
        name="all-set",
    )
    _patch_session(monkeypatch, [pol])
    result = evaluate_policy("set_light", {"any": "arg"})
    assert result.requires_confirm is False


# ---------------------------------------------------------------------------
# DB failure fallback
# ---------------------------------------------------------------------------


def test_db_failure_defaults_to_confirm(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_session_error(monkeypatch)
    result = evaluate_policy("set_device_capability", {})
    assert result.requires_confirm is True
    assert result.policy_name == "<policy-lookup-failed>"
    assert result.impact_level == "unknown"


# ---------------------------------------------------------------------------
# use_tool meta-tool
# ---------------------------------------------------------------------------


def test_use_tool_no_match_builds_inner_message(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_session(monkeypatch, [])
    result = evaluate_policy("use_tool", {"name": "turn_on_lights"})
    assert "turn_on_lights" in result.confirm_message
    assert result.requires_confirm is True


def test_use_tool_matched_policy_builds_inner_message(monkeypatch: pytest.MonkeyPatch) -> None:
    pol = _policy(
        tool_pattern="use_tool",
        requires_confirm=True,
        name="use_tool-policy",
        confirm_message="This will be replaced by dynamic message.",
    )
    _patch_session(monkeypatch, [pol])
    result = evaluate_policy("use_tool", {"name": "lock_front_door"})
    assert "lock_front_door" in result.confirm_message


# ---------------------------------------------------------------------------
# _build_confirm_message
# ---------------------------------------------------------------------------


def test_build_confirm_message_use_tool_with_name() -> None:
    msg = _build_confirm_message("use_tool", {"name": "ring_bell"})
    assert msg == "Execute Homey action 'ring_bell'?"


def test_build_confirm_message_use_tool_without_name() -> None:
    msg = _build_confirm_message("use_tool", {})
    assert "use_tool" in msg


def test_build_confirm_message_other_tool() -> None:
    msg = _build_confirm_message("lock_door", {"device_id": "abc"})
    assert "lock_door" in msg
