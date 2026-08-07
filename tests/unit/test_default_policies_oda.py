"""Unit tests for the Oda entries in app.policy.default_policies —
verifies both the raw DEFAULT_POLICIES content and that app.policy.gate's
evaluate_policy() actually produces the intended decision for each Oda
tool once those entries are seeded.

See docs/oda-grocery-mcp-tool-design.md "Policy gate additions": every Oda
tool must have an explicit entry (the read-tool prefix fallback doesn't
cover several of them), reads + feedback auto-allow, manipulate_cart /
select_delivery_slot require confirmation with a non-generic message.
"""

from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import MagicMock

import pytest

from app.policy.default_policies import _ODA_AUTO_ALLOW_TOOLS, DEFAULT_POLICIES
from app.policy.gate import evaluate_policy

_ODA_CONFIRM_TOOLS = ["manipulate_cart", "select_delivery_slot"]


def _oda_policy_entries() -> list[dict[str, object]]:
    return [p for p in DEFAULT_POLICIES if str(p["name"]).startswith("Oda ")]


# ---------------------------------------------------------------------------
# Raw DEFAULT_POLICIES content
# ---------------------------------------------------------------------------


def test_every_auto_allow_tool_has_an_entry() -> None:
    patterns = {p["tool_pattern"] for p in _oda_policy_entries()}
    for tool in _ODA_AUTO_ALLOW_TOOLS:
        assert tool in patterns, f"missing default policy for {tool!r}"


def test_auto_allow_entries_do_not_require_confirmation() -> None:
    by_pattern = {p["tool_pattern"]: p for p in _oda_policy_entries()}
    for tool in _ODA_AUTO_ALLOW_TOOLS:
        assert by_pattern[tool]["requires_confirm"] is False
        assert by_pattern[tool]["impact_level"] == "low"


@pytest.mark.parametrize("tool", _ODA_CONFIRM_TOOLS)
def test_cart_and_slot_writes_require_confirmation_with_a_real_message(tool: str) -> None:
    by_pattern = {p["tool_pattern"]: p for p in _oda_policy_entries()}
    entry = by_pattern[tool]
    assert entry["requires_confirm"] is True
    # Must not be empty — an empty confirm_message falls back to gate.py's
    # generic (Homey-worded) default, which would be wrong here.
    assert entry["confirm_message"]
    assert "homey" not in str(entry["confirm_message"]).lower()


def test_no_duplicate_policy_names() -> None:
    names = [p["name"] for p in DEFAULT_POLICIES]
    assert len(names) == len(set(names))


def test_confirm_tools_are_not_also_in_auto_allow_list() -> None:
    for tool in _ODA_CONFIRM_TOOLS:
        assert tool not in _ODA_AUTO_ALLOW_TOOLS


# ---------------------------------------------------------------------------
# Behaviour through evaluate_policy(), as if seeded into the DB
# ---------------------------------------------------------------------------


def _mock_policy_from_entry(entry: dict[str, object]) -> MagicMock:
    p = MagicMock()
    for key, value in entry.items():
        setattr(p, key, value)
    p.enabled = True
    return p


def _patch_session_with_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mirrors the real seeded-policy query order: requires_confirm DESC,
    name ASC (see app.policy.gate.evaluate_policy's query comment) —
    confirmation-required rows must be checked before broader ones."""
    ordered = sorted(DEFAULT_POLICIES, key=lambda p: (not p["requires_confirm"], str(p["name"])))
    mocks = [_mock_policy_from_entry(p) for p in ordered]

    @contextmanager
    def _fake_session():  # type: ignore[misc]
        session = MagicMock()
        session.exec.return_value.all.return_value = mocks
        yield session

    monkeypatch.setattr("app.policy.gate.users_session", _fake_session)


@pytest.mark.parametrize("tool", _ODA_AUTO_ALLOW_TOOLS)
def test_auto_allow_tools_do_not_require_confirmation(
    monkeypatch: pytest.MonkeyPatch, tool: str
) -> None:
    _patch_session_with_defaults(monkeypatch)
    decision = evaluate_policy(tool, {})
    assert decision.requires_confirm is False


@pytest.mark.parametrize("tool", _ODA_CONFIRM_TOOLS)
def test_cart_and_slot_tools_require_confirmation(
    monkeypatch: pytest.MonkeyPatch, tool: str
) -> None:
    _patch_session_with_defaults(monkeypatch)
    decision = evaluate_policy(tool, {"operations": [{"quantity": 1, "productId": 123}]})
    assert decision.requires_confirm is True
    assert decision.confirm_message
    assert "homey" not in decision.confirm_message.lower()


def test_unrelated_unmatched_tool_still_falls_back_to_conservative_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sanity check that adding the Oda entries didn't accidentally loosen
    the gate for tools that don't belong to Oda at all."""
    _patch_session_with_defaults(monkeypatch)
    decision = evaluate_policy("some_future_write_tool", {})
    assert decision.requires_confirm is True
    assert decision.policy_name == "<unmatched>"
