"""Unit tests for app.oda.mcp_client — OdaTokenAuth's httpx.Auth flow, the
policy-gated process_tool_call callback, and start_mcp()/stop_mcp()'s
graceful no-op paths (no household / not connected).

The actual successful-MCP-connection path (MCPToolset.__aenter__ +
list_tools against a real or fake server) isn't covered here, matching the
existing (untested) state of app.homey.mcp_client.start_mcp()'s success
path in this codebase — out of scope for this pass.
"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager

import httpx
import pytest
from sqlmodel import Session

from app.oda import mcp_client


class _FakeDeps:
    def __init__(self, **overrides: object) -> None:
        self.run_id = overrides.get("run_id", "run-1")
        self.household_id = overrides.get("household_id", "hh-1")
        self.user_id = overrides.get("user_id", "user-1")
        self.channel_user_id = overrides.get("channel_user_id", "12345")
        self.channel = overrides.get("channel", "telegram")


class _FakeCtx:
    def __init__(self, deps: _FakeDeps) -> None:
        self.deps = deps


def _decision(*, requires_confirm: bool, **overrides: object) -> object:
    from app.policy.gate import PolicyDecision

    defaults: dict[str, object] = dict(
        requires_confirm=requires_confirm,
        policy_name="test-policy",
        confirm_message="Confirm this?",
        impact_level="low",
    )
    defaults.update(overrides)
    return PolicyDecision(**defaults)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# OdaTokenAuth
# ---------------------------------------------------------------------------


async def test_attaches_bearer_token_when_available(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_get_token(household_id: str, provider: str) -> str | None:
        assert household_id == "hh-1"
        assert provider == "oda"
        return "token-abc"

    monkeypatch.setattr("app.integrations.accounts.get_valid_access_token", _fake_get_token)

    auth = mcp_client.OdaTokenAuth("hh-1")
    request = httpx.Request("GET", "https://oda.com/mcp")
    gen = auth.async_auth_flow(request)
    sent = await gen.__anext__()
    assert sent.headers["Authorization"] == "Bearer token-abc"

    ok_response = httpx.Response(200, request=sent)
    with pytest.raises(StopAsyncIteration):
        await gen.asend(ok_response)


async def test_no_authorization_header_when_not_connected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake_get_token(household_id: str, provider: str) -> str | None:
        return None

    monkeypatch.setattr("app.integrations.accounts.get_valid_access_token", _fake_get_token)

    auth = mcp_client.OdaTokenAuth("hh-1")
    request = httpx.Request("GET", "https://oda.com/mcp")
    gen = auth.async_auth_flow(request)
    sent = await gen.__anext__()
    assert "Authorization" not in sent.headers


async def test_refreshes_and_retries_once_on_401(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []

    async def _fake_get_token(household_id: str, provider: str) -> str | None:
        calls.append(1)
        return f"token-{len(calls)}"

    monkeypatch.setattr("app.integrations.accounts.get_valid_access_token", _fake_get_token)

    auth = mcp_client.OdaTokenAuth("hh-1")
    request = httpx.Request("GET", "https://oda.com/mcp")
    gen = auth.async_auth_flow(request)

    first = await gen.__anext__()
    assert first.headers["Authorization"] == "Bearer token-1"

    unauthorized = httpx.Response(401, request=first)
    second = await gen.asend(unauthorized)
    assert second.headers["Authorization"] == "Bearer token-2"
    assert len(calls) == 2

    ok = httpx.Response(200, request=second)
    with pytest.raises(StopAsyncIteration):
        await gen.asend(ok)


# ---------------------------------------------------------------------------
# _resolve_household_id
# ---------------------------------------------------------------------------


@pytest.fixture
def db(monkeypatch: pytest.MonkeyPatch, in_memory_engine: object) -> Session:
    @contextmanager  # type: ignore[misc]
    def _session():
        with Session(in_memory_engine) as s:  # type: ignore[arg-type]
            yield s

    monkeypatch.setattr("app.db.users_session", _session)
    # app.integrations.accounts imports users_session at module level
    # (`from app.db import users_session`), so patching app.db.users_session
    # alone doesn't affect its already-bound reference — get_account() would
    # otherwise fall through to the real DB. See test_integrations_accounts.py,
    # which patches this same module-local binding for the same reason.
    monkeypatch.setattr("app.integrations.accounts.users_session", _session)
    with Session(in_memory_engine) as s:  # type: ignore[arg-type]
        yield s


def test_resolve_household_id_returns_none_when_no_household(db: Session) -> None:
    assert mcp_client._resolve_household_id() is None


def test_resolve_household_id_returns_the_household(db: Session) -> None:
    from app.models.users import Household

    db.add(Household(id="hh-1", name="The Home"))
    db.commit()
    assert mcp_client._resolve_household_id() == "hh-1"


# ---------------------------------------------------------------------------
# start_mcp() / stop_mcp() graceful no-op paths
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_module_state() -> None:
    mcp_client._mcp_server = None
    yield
    mcp_client._mcp_server = None


async def test_start_mcp_noop_when_no_household(db: Session) -> None:
    result = await mcp_client.start_mcp()
    assert result is None
    assert mcp_client.get_mcp_server() is None


async def test_start_mcp_noop_when_household_not_connected(db: Session) -> None:
    from app.models.users import Household

    db.add(Household(id="hh-1", name="The Home"))
    db.commit()

    result = await mcp_client.start_mcp()
    assert result is None
    assert mcp_client.get_mcp_server() is None


async def test_stop_mcp_is_a_safe_noop_when_never_started() -> None:
    await mcp_client.stop_mcp()  # should not raise
    assert mcp_client.get_mcp_server() is None


# ---------------------------------------------------------------------------
# _policy_process_tool_call
# ---------------------------------------------------------------------------


async def test_auto_allow_tool_executes_and_returns_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.policy.gate.evaluate_policy", lambda *a, **kw: _decision(requires_confirm=False)
    )

    async def _call_tool(tool_name: str, tool_args: dict) -> str:  # type: ignore[type-arg]
        return "cart contents here"

    ctx = _FakeCtx(_FakeDeps())
    result = await mcp_client._policy_process_tool_call(ctx, _call_tool, "get_cart", {})
    assert result == "cart contents here"


async def test_auto_allow_tool_truncates_large_results(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.policy.gate.evaluate_policy", lambda *a, **kw: _decision(requires_confirm=False)
    )
    huge = "x" * (mcp_client._MAX_TOOL_RESULT_CHARS + 500)

    async def _call_tool(tool_name: str, tool_args: dict) -> str:  # type: ignore[type-arg]
        return huge

    ctx = _FakeCtx(_FakeDeps())
    result = await mcp_client._policy_process_tool_call(ctx, _call_tool, "get_cart", {})
    assert len(result) <= mcp_client._MAX_TOOL_RESULT_CHARS + len("\n[...truncated]")
    assert result.endswith("[...truncated]")


async def test_auto_allow_tool_times_out(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import get_settings

    monkeypatch.setenv("ODA_TOOL_TIMEOUT_SECS", "0")
    get_settings.cache_clear()
    monkeypatch.setattr(
        "app.policy.gate.evaluate_policy", lambda *a, **kw: _decision(requires_confirm=False)
    )

    async def _call_tool(tool_name: str, tool_args: dict) -> str:  # type: ignore[type-arg]
        await asyncio.sleep(0.05)
        return "too slow"

    ctx = _FakeCtx(_FakeDeps())
    result = await mcp_client._policy_process_tool_call(ctx, _call_tool, "get_cart", {})
    assert "did not respond within" in result
    get_settings.cache_clear()


async def test_confirm_required_saves_pending_action_and_notifies_channel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.policy.gate.evaluate_policy",
        lambda *a, **kw: _decision(
            requires_confirm=True, policy_name="Oda manipulate_cart", confirm_message="Update cart?"
        ),
    )

    save_calls: list[dict[str, object]] = []

    def _fake_save_pending_action(**kwargs: object) -> str:
        save_calls.append(kwargs)
        return "token-xyz"

    monkeypatch.setattr("app.policy.pending.save_pending_action", _fake_save_pending_action)

    sent_prompts: list[tuple[str, str, str]] = []

    class _FakeChannel:
        async def send_confirmation_prompt(
            self, channel_user_id: str, message: str, token: str
        ) -> None:
            sent_prompts.append((channel_user_id, message, token))

    monkeypatch.setattr("app.channels.registry.get_channel", lambda name: _FakeChannel())

    async def _call_tool(tool_name: str, tool_args: dict) -> str:  # type: ignore[type-arg]
        raise AssertionError("call_tool must not run when confirmation is required")

    ctx = _FakeCtx(_FakeDeps())
    result = await mcp_client._policy_process_tool_call(
        ctx, _call_tool, "manipulate_cart", {"operations": []}
    )

    assert "requires your confirmation" in result
    assert len(save_calls) == 1
    assert save_calls[0]["tool_name"] == "manipulate_cart"
    assert len(sent_prompts) == 1
    assert sent_prompts[0] == ("12345", "Update cart?", "token-xyz")


async def test_confirm_required_blocks_when_deps_incomplete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.policy.gate.evaluate_policy", lambda *a, **kw: _decision(requires_confirm=True)
    )

    async def _call_tool(tool_name: str, tool_args: dict) -> str:  # type: ignore[type-arg]
        raise AssertionError("must not be called")

    ctx = _FakeCtx(_FakeDeps(household_id="", user_id=""))
    result = await mcp_client._policy_process_tool_call(ctx, _call_tool, "manipulate_cart", {})
    assert "session context is incomplete" in result
