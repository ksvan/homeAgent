"""Unit tests for Phase 2's Telegram identity resolution and account
linking in app.bot — see
docs/household-identity-and-access-design.md Option B.

Covers: ChannelMapping-first resolution (so a web-only User created via a
passkey invite can later be reached over Telegram without ever getting a
throwaway placeholder auto-created), the /link command's collision/
expiry/success paths, and the authorize() gate on Telegram ingress.
"""

from __future__ import annotations

from contextlib import contextmanager

import pytest
from sqlmodel import Session, select

import app.bot as bot_module
from app.models.users import ChannelMapping, Household, User


@pytest.fixture(autouse=True)
def patch_users_session(monkeypatch: pytest.MonkeyPatch, in_memory_engine: object) -> None:
    @contextmanager
    def _session():  # type: ignore[misc]
        with Session(in_memory_engine) as s:  # type: ignore[arg-type]
            yield s

    monkeypatch.setattr(bot_module, "users_session", _session)
    monkeypatch.setattr("app.db.users_session", _session)
    # app.world.repository imports users_session at module level (its own
    # bound copy, unaffected by patching app.db.users_session above) — the
    # auto-create-user path below calls into WorldModelRepository.upsert_member,
    # which would otherwise hit the real on-disk data/db/users.db.
    monkeypatch.setattr("app.world.repository.users_session", _session)

    with _session() as db:
        db.add(Household(id="hh-1", name="The Home"))
        db.commit()

    yield


@pytest.fixture(autouse=True)
def clear_rate_cache() -> None:
    bot_module._user_call_times.clear()


def _add_user(**overrides: object) -> User:
    defaults: dict[str, object] = dict(
        id="user-1", household_id="hh-1", name="Kristian", telegram_id=None
    )
    defaults.update(overrides)
    user = User(**defaults)  # type: ignore[arg-type]
    with bot_module.users_session() as db:
        db.add(user)
        db.commit()
        db.refresh(user)
        db.expunge(user)
    return user


def _add_mapping(user_id: str, telegram_id: int) -> None:
    with bot_module.users_session() as db:
        db.add(
            ChannelMapping(user_id=user_id, channel="telegram", channel_user_id=str(telegram_id))
        )
        db.commit()


# ---------------------------------------------------------------------------
# _get_or_create_user resolution order
# ---------------------------------------------------------------------------


def test_resolves_via_channel_mapping_for_web_only_user() -> None:
    """A User created web-only (no telegram_id) becomes reachable over
    Telegram once a ChannelMapping links it — without ever touching
    User.telegram_id-matching logic."""
    _add_user(id="user-1", telegram_id=None)
    _add_mapping("user-1", 5001)

    info = bot_module._get_or_create_user(5001)

    assert info.id == "user-1"


def test_resolves_via_legacy_telegram_id_when_no_mapping_exists() -> None:
    _add_user(id="user-1", telegram_id=6001)

    info = bot_module._get_or_create_user(6001)

    assert info.id == "user-1"


def test_auto_creates_new_user_for_unknown_telegram_id() -> None:
    info = bot_module._get_or_create_user(7001)

    with bot_module.users_session() as db:
        user = db.exec(select(User).where(User.id == info.id)).first()
        assert user is not None
        assert user.telegram_id == 7001
        mapping = db.exec(
            select(ChannelMapping).where(
                ChannelMapping.channel == "telegram", ChannelMapping.channel_user_id == "7001"
            )
        ).first()
        assert mapping is not None
        assert mapping.user_id == info.id


# ---------------------------------------------------------------------------
# /link command
# ---------------------------------------------------------------------------


async def test_link_without_code_shows_usage(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.control.audit.record_audit_event", lambda *a, **k: None)
    result = await bot_module._handle_link_command(9001, "/link")
    assert "Usage" in result


async def test_link_with_valid_code_creates_mapping_and_backfills_telegram_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    audited: list[str] = []
    monkeypatch.setattr(
        "app.control.audit.record_audit_event",
        lambda event_type, *a, **k: audited.append(event_type),
    )
    _add_user(id="user-1", name="Alice", telegram_id=None)

    from app.webchat.link_codes import LinkCodeInfo

    monkeypatch.setattr(
        "app.webchat.link_codes.consume_link_code",
        lambda code: LinkCodeInfo(
            code=code, user_id="user-1", household_id="hh-1", expires_at=None
        ),  # type: ignore[arg-type]
    )

    result = await bot_module._handle_link_command(9002, "/link ABCDEFGHJKMN")

    assert "Alice" in result
    with bot_module.users_session() as db:
        user = db.exec(select(User).where(User.id == "user-1")).first()
        assert user is not None
        assert user.telegram_id == 9002
        mapping = db.exec(
            select(ChannelMapping).where(
                ChannelMapping.channel == "telegram", ChannelMapping.channel_user_id == "9002"
            )
        ).first()
        assert mapping is not None
        assert mapping.user_id == "user-1"
    assert "telegram.link_succeeded" in audited


async def test_link_rejects_already_linked_telegram_id(monkeypatch: pytest.MonkeyPatch) -> None:
    audited: list[str] = []
    monkeypatch.setattr(
        "app.control.audit.record_audit_event",
        lambda event_type, *a, **k: audited.append(event_type),
    )
    _add_user(id="user-1", telegram_id=9003)

    called = {"consumed": False}

    def _fake_consume(code: str) -> None:
        called["consumed"] = True
        return None

    monkeypatch.setattr("app.webchat.link_codes.consume_link_code", _fake_consume)

    result = await bot_module._handle_link_command(9003, "/link ANYCODE1234")

    assert "already linked" in result.lower()
    assert called["consumed"] is False  # rejected before ever touching the code
    assert "telegram.link_rejected_already_linked" in audited


async def test_link_rejects_invalid_or_expired_code(monkeypatch: pytest.MonkeyPatch) -> None:
    audited: list[str] = []
    monkeypatch.setattr(
        "app.control.audit.record_audit_event",
        lambda event_type, *a, **k: audited.append(event_type),
    )
    monkeypatch.setattr("app.webchat.link_codes.consume_link_code", lambda code: None)

    result = await bot_module._handle_link_command(9004, "/link BADCODE1234")

    assert "invalid or has expired" in result.lower()
    assert "telegram.link_failed" in audited


# ---------------------------------------------------------------------------
# authorize() gate on Telegram ingress
# ---------------------------------------------------------------------------


async def test_handle_incoming_message_drops_silently_when_telegram_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = bot_module.get_settings()
    monkeypatch.setattr(settings, "allowed_telegram_ids", [9005])
    monkeypatch.setattr(settings, "app_env", "production")
    _add_user(id="user-1", telegram_id=9005, telegram_enabled=False)

    called = {"count": 0}

    async def _fake_agent_run(**kwargs: object) -> object:
        called["count"] += 1
        raise AssertionError("agent_run should not be called for a disabled surface")

    monkeypatch.setattr("app.agent.runner.agent_run", _fake_agent_run)

    result = await bot_module.handle_incoming_message(9005, "hello", [])

    assert result is None
    assert called["count"] == 0


async def test_handle_incoming_message_proceeds_when_telegram_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = bot_module.get_settings()
    monkeypatch.setattr(settings, "allowed_telegram_ids", [9006])
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "rate_limit_per_user_per_minute", 100)
    _add_user(id="user-1", telegram_id=9006, telegram_enabled=True)

    from app.agent.runner import RunOutcome

    async def _fake_agent_run(**kwargs: object) -> RunOutcome:
        return RunOutcome(response="hi there", success=True, duration_ms=1, run_id="run-1")

    monkeypatch.setattr("app.agent.runner.agent_run", _fake_agent_run)
    monkeypatch.setattr("app.memory.conversation.save_message_pair", lambda *a, **k: None)
    monkeypatch.setattr(
        "app.homey.state_cache.update_snapshots_from_tool_calls", lambda *a, **k: None
    )

    result = await bot_module.handle_incoming_message(9006, "hello", [])

    assert result is not None
    assert "hi there" in result
