"""Unit tests for app.policy.confirm — shared PendingAction confirm/cancel.

Both Telegram (inline buttons) and web chat (WS confirm/cancel messages)
drive this module. All DB/MCP/side-effect calls are monkeypatched at their
source modules, since app.policy.confirm imports them lazily inside each
function (see module docstring — done to avoid import cycles).
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.policy.confirm import cancel_pending_action_for_user, execute_pending_action


def _make_action(**overrides: object) -> object:
    from app.models.cache import PendingAction

    defaults: dict[str, object] = dict(
        token="tok-1",
        household_id="hh-1",
        user_id="user-1",
        tool_name="set_light",
        tool_args='{"device_id": "dev-1", "capability": "onoff", "value": true}',
        policy_name="lights",
        expires_at=datetime.utcnow() + timedelta(seconds=60),
    )
    defaults.update(overrides)
    return PendingAction(**defaults)  # type: ignore[arg-type]


class _FakeMcpServer:
    def __init__(self, result: object = "ok", raise_exc: Exception | None = None) -> None:
        self.result = result
        self.raise_exc = raise_exc
        self.calls: list[tuple[str, dict[str, object]]] = []

    async def direct_call_tool(self, tool_name: str, tool_args: dict[str, object]) -> object:
        self.calls.append((tool_name, tool_args))
        if self.raise_exc:
            raise self.raise_exc
        return self.result


@pytest.fixture(autouse=True)
def patch_side_effects(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    """Patch the modules app.policy.confirm imports lazily. Returns a dict of
    call-recorders so tests can assert on them."""
    calls: dict[str, object] = {"deleted": [], "saved_pairs": [], "verify_scheduled": []}

    monkeypatch.setattr(
        "app.policy.pending.delete_pending_action", lambda token: calls["deleted"].append(token)
    )
    monkeypatch.setattr(
        "app.memory.conversation.save_message_pair",
        lambda user_id, in_text, out_text: calls["saved_pairs"].append(
            (user_id, in_text, out_text)
        ),
    )

    async def _fake_verify(
        household_id,
        channel_user_id,
        tool_name,
        tool_args,
        control_task_id=None,
        channel="telegram",
    ):  # type: ignore[no-untyped-def]
        calls["verify_scheduled"].append((household_id, channel_user_id, tool_name, channel))

    monkeypatch.setattr("app.homey.verify.verify_after_write", _fake_verify)
    return calls


def _patch_pending_action(monkeypatch: pytest.MonkeyPatch, action: object | None) -> None:
    monkeypatch.setattr("app.policy.pending.get_pending_action", lambda token: action)


def _patch_mcp_server(monkeypatch: pytest.MonkeyPatch, server: object | None) -> None:
    monkeypatch.setattr("app.homey.mcp_client.get_mcp_server", lambda: server)


def _patch_oda_mcp_server(monkeypatch: pytest.MonkeyPatch, server: object | None) -> None:
    monkeypatch.setattr("app.oda.mcp_client.get_mcp_server", lambda: server)


# ---------------------------------------------------------------------------
# execute_pending_action
# ---------------------------------------------------------------------------


async def test_execute_returns_expired_when_action_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_pending_action(monkeypatch, None)

    result = await execute_pending_action("tok-1", "user-1", "chan-1")

    assert result.status == "expired"
    assert result.ok is False


async def test_execute_rejects_non_owner(
    monkeypatch: pytest.MonkeyPatch, patch_side_effects: dict
) -> None:
    action = _make_action(user_id="user-1")
    _patch_pending_action(monkeypatch, action)

    result = await execute_pending_action("tok-1", "someone-else", "chan-1")

    assert result.status == "not_owner"
    assert result.ok is False
    # Ownership check fails before any deletion/execution happens
    assert patch_side_effects["deleted"] == []


async def test_execute_rejects_empty_requesting_user_id(monkeypatch: pytest.MonkeyPatch) -> None:
    action = _make_action(user_id="user-1")
    _patch_pending_action(monkeypatch, action)

    result = await execute_pending_action("tok-1", "", "chan-1")

    assert result.status == "not_owner"


async def test_execute_reports_failed_when_homey_disconnected(
    monkeypatch: pytest.MonkeyPatch, patch_side_effects: dict
) -> None:
    action = _make_action()
    _patch_pending_action(monkeypatch, action)
    _patch_mcp_server(monkeypatch, None)

    result = await execute_pending_action("tok-1", "user-1", "chan-1")

    assert result.status == "failed"
    assert result.ok is False
    # Still deleted — action is consumed even though execution couldn't proceed
    assert patch_side_effects["deleted"] == ["tok-1"]


async def test_execute_success_calls_tool_saves_history_and_schedules_verify(
    monkeypatch: pytest.MonkeyPatch, patch_side_effects: dict
) -> None:
    action = _make_action()
    _patch_pending_action(monkeypatch, action)
    server = _FakeMcpServer(result="done")
    _patch_mcp_server(monkeypatch, server)

    result = await execute_pending_action("tok-1", "user-1", "chan-1", channel="web")
    # verify_after_write is scheduled via asyncio.ensure_future — let it run
    import asyncio

    await asyncio.sleep(0)

    assert result.ok is True
    assert result.status == "executed"
    assert "done" in result.message
    assert server.calls == [
        ("set_light", {"device_id": "dev-1", "capability": "onoff", "value": True})
    ]
    assert patch_side_effects["deleted"] == ["tok-1"]
    assert len(patch_side_effects["saved_pairs"]) == 1
    assert patch_side_effects["saved_pairs"][0][0] == "user-1"
    assert patch_side_effects["verify_scheduled"] == [("hh-1", "chan-1", "set_light", "web")]


async def test_execute_success_with_large_result_shows_generic_done(
    monkeypatch: pytest.MonkeyPatch, patch_side_effects: dict
) -> None:
    """Regression coverage: confirming an Oda manipulate_cart used to dump
    the entire raw cart JSON into the chat as the confirmation outcome
    ("Done: {'groups': [...huge...]}"). Large results should collapse to a
    plain "Done." instead."""
    action = _make_action(provider="oda", tool_name="manipulate_cart")
    _patch_pending_action(monkeypatch, action)
    huge_result = {"groups": [{"items": [{"product": {"name": "x"}}] * 50}]}
    server = _FakeMcpServer(result=huge_result)
    _patch_oda_mcp_server(monkeypatch, server)

    result = await execute_pending_action("tok-1", "user-1", "chan-1")

    assert result.ok is True
    assert result.message == "Done."


async def test_execute_success_with_short_result_shows_it(
    monkeypatch: pytest.MonkeyPatch, patch_side_effects: dict
) -> None:
    action = _make_action(provider="oda", tool_name="manipulate_cart")
    _patch_pending_action(monkeypatch, action)
    server = _FakeMcpServer(result="ok")
    _patch_oda_mcp_server(monkeypatch, server)

    result = await execute_pending_action("tok-1", "user-1", "chan-1")

    assert result.ok is True
    assert result.message == "Done: ok"


async def test_execute_tool_exception_reports_failure_and_saves_failure_pair(
    monkeypatch: pytest.MonkeyPatch, patch_side_effects: dict
) -> None:
    action = _make_action()
    _patch_pending_action(monkeypatch, action)
    server = _FakeMcpServer(raise_exc=RuntimeError("homey timeout"))
    _patch_mcp_server(monkeypatch, server)

    result = await execute_pending_action("tok-1", "user-1", "chan-1")

    assert result.ok is False
    assert result.status == "failed"
    assert patch_side_effects["deleted"] == ["tok-1"]
    assert len(patch_side_effects["saved_pairs"]) == 1
    assert "failed" in patch_side_effects["saved_pairs"][0][2]
    # No verify scheduled — the write itself never succeeded
    assert patch_side_effects["verify_scheduled"] == []


# ---------------------------------------------------------------------------
# execute_pending_action — Oda provider dispatch
#
# Regression coverage: execute_pending_action used to be hardcoded to
# app.homey.mcp_client.get_mcp_server() regardless of which server the tool
# actually belonged to, so a confirmed Oda action (e.g. manipulate_cart)
# would always fail with "tool not found" on the Homey server, surfaced to
# the user as a generic "Action failed — please check the device and try
# again." This is exactly the bug seen in production once Oda confirmations
# started firing (Phase 4 added Oda's confirm-required policies but never
# updated this shared execute path — the PendingAction row had no concept
# of which server it belonged to).
# ---------------------------------------------------------------------------


async def test_execute_dispatches_to_oda_server_for_oda_provider(
    monkeypatch: pytest.MonkeyPatch, patch_side_effects: dict
) -> None:
    action = _make_action(
        provider="oda",
        tool_name="manipulate_cart",
        tool_args='{"operations": [{"quantity": 1, "productId": 69388}]}',
        policy_name="Oda manipulate_cart",
    )
    _patch_pending_action(monkeypatch, action)
    homey_server = _FakeMcpServer()
    _patch_mcp_server(monkeypatch, homey_server)
    oda_server = _FakeMcpServer(result="cart updated")
    _patch_oda_mcp_server(monkeypatch, oda_server)

    result = await execute_pending_action("tok-1", "user-1", "chan-1")

    assert result.ok is True
    assert result.status == "executed"
    assert "cart updated" in result.message
    assert oda_server.calls == [
        ("manipulate_cart", {"operations": [{"quantity": 1, "productId": 69388}]})
    ]
    # The Homey server must never be touched for an Oda-owned action.
    assert homey_server.calls == []
    # No device-state verification for Oda — see confirm.py's comment.
    assert patch_side_effects["verify_scheduled"] == []


async def test_execute_reports_failed_when_oda_disconnected(
    monkeypatch: pytest.MonkeyPatch, patch_side_effects: dict
) -> None:
    action = _make_action(provider="oda", tool_name="manipulate_cart")
    _patch_pending_action(monkeypatch, action)
    _patch_oda_mcp_server(monkeypatch, None)

    result = await execute_pending_action("tok-1", "user-1", "chan-1")

    assert result.status == "failed"
    assert result.message == "Oda is not connected — cannot execute."
    assert patch_side_effects["deleted"] == ["tok-1"]


async def test_execute_oda_failure_message_does_not_mention_device(
    monkeypatch: pytest.MonkeyPatch, patch_side_effects: dict
) -> None:
    action = _make_action(provider="oda", tool_name="manipulate_cart")
    _patch_pending_action(monkeypatch, action)
    server = _FakeMcpServer(raise_exc=RuntimeError("oda unavailable"))
    _patch_oda_mcp_server(monkeypatch, server)

    result = await execute_pending_action("tok-1", "user-1", "chan-1")

    assert result.ok is False
    assert result.status == "failed"
    assert "device" not in result.message.lower()


async def test_execute_defaults_to_homey_when_provider_missing(
    monkeypatch: pytest.MonkeyPatch, patch_side_effects: dict
) -> None:
    # Rows saved before the provider column existed have an empty/None value
    # in practice once read back — must still behave like "homey", not crash.
    action = _make_action()
    action.provider = ""  # simulate a pre-migration row
    _patch_pending_action(monkeypatch, action)
    server = _FakeMcpServer(result="done")
    _patch_mcp_server(monkeypatch, server)

    result = await execute_pending_action("tok-1", "user-1", "chan-1")

    assert result.ok is True
    assert server.calls == [
        ("set_light", {"device_id": "dev-1", "capability": "onoff", "value": True})
    ]


# ---------------------------------------------------------------------------
# cancel_pending_action_for_user
# ---------------------------------------------------------------------------


async def test_cancel_returns_expired_when_action_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_pending_action(monkeypatch, None)

    result = await cancel_pending_action_for_user("tok-1", "user-1")

    assert result.status == "expired"


async def test_cancel_rejects_non_owner(
    monkeypatch: pytest.MonkeyPatch, patch_side_effects: dict
) -> None:
    action = _make_action(user_id="user-1")
    _patch_pending_action(monkeypatch, action)

    result = await cancel_pending_action_for_user("tok-1", "someone-else")

    assert result.status == "not_owner"
    assert patch_side_effects["deleted"] == []


async def test_cancel_success_deletes_action(
    monkeypatch: pytest.MonkeyPatch, patch_side_effects: dict
) -> None:
    action = _make_action(user_id="user-1")
    _patch_pending_action(monkeypatch, action)

    result = await cancel_pending_action_for_user("tok-1", "user-1")

    assert result.ok is True
    assert result.status == "cancelled"
    assert patch_side_effects["deleted"] == ["tok-1"]
