"""Unit tests for app.integrations.accounts — household-scoped
IntegrationAccount repository + single-flight token refresh.

app.integrations.accounts.users_session is monkeypatched to an in-memory
SQLite engine (see tests/conftest.py's in_memory_engine fixture). Refresh
tests monkeypatch app.oda.oauth directly so no network call happens.
"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest
from sqlmodel import Session

from app.integrations import accounts
from app.integrations.crypto import decrypt
from app.oda.oauth import OdaOAuthError, TokenResponse


@pytest.fixture(autouse=True)
def _secret_key(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("APP_SECRET_KEY", "unit-test-secret-key-not-a-real-fernet-key")
    yield
    get_settings.cache_clear()


@pytest.fixture
def db(monkeypatch: pytest.MonkeyPatch, in_memory_engine: object) -> Session:
    @contextmanager  # type: ignore[misc]
    def _session():
        with Session(in_memory_engine) as s:  # type: ignore[arg-type]
            yield s

    monkeypatch.setattr("app.integrations.accounts.users_session", _session)
    # Each test gets an isolated refresh-lock map too.
    monkeypatch.setattr(accounts, "_refresh_locks", {})
    with Session(in_memory_engine) as s:  # type: ignore[arg-type]
        yield s


def _seed_household_and_user(db: Session) -> tuple[str, str]:
    from app.models.users import Household, User

    household = Household(name="Test household")
    db.add(household)
    db.commit()
    db.refresh(household)

    user = User(household_id=household.id, telegram_id=123, name="Parent")
    db.add(user)
    db.commit()
    db.refresh(user)
    return household.id, user.id


def test_get_account_returns_none_when_not_connected(db: Session) -> None:
    assert accounts.get_account("hh-1", "oda") is None


def test_upsert_then_get_round_trips(db: Session) -> None:
    household_id, user_id = _seed_household_and_user(db)
    expires_at = datetime.now(timezone.utc) + timedelta(hours=1)

    accounts.upsert_account(
        household_id=household_id,
        provider="oda",
        connected_by_user_id=user_id,
        client_id="client-abc",
        client_secret="client-secret",
        access_token="access-token-value",
        refresh_token="refresh-token-value",
        expires_at=expires_at,
    )

    account = accounts.get_account(household_id, "oda")
    assert account is not None
    assert account.client_id == "client-abc"
    assert decrypt(account.access_token) == "access-token-value"
    assert decrypt(account.refresh_token) == "refresh-token-value"
    assert decrypt(account.client_secret) == "client-secret"


def test_upsert_replaces_existing_row_on_reconnect(db: Session) -> None:
    household_id, user_id = _seed_household_and_user(db)
    expires_at = datetime.now(timezone.utc) + timedelta(hours=1)

    accounts.upsert_account(
        household_id=household_id,
        provider="oda",
        connected_by_user_id=user_id,
        client_id="client-1",
        client_secret="",
        access_token="token-1",
        refresh_token="refresh-1",
        expires_at=expires_at,
    )
    accounts.upsert_account(
        household_id=household_id,
        provider="oda",
        connected_by_user_id=user_id,
        client_id="client-2",
        client_secret="",
        access_token="token-2",
        refresh_token="refresh-2",
        expires_at=expires_at,
    )

    account = accounts.get_account(household_id, "oda")
    assert account is not None
    assert account.client_id == "client-2"
    assert decrypt(account.access_token) == "token-2"


def test_delete_account_removes_row(db: Session) -> None:
    household_id, user_id = _seed_household_and_user(db)
    accounts.upsert_account(
        household_id=household_id,
        provider="oda",
        connected_by_user_id=user_id,
        client_id="client-abc",
        client_secret="",
        access_token="token",
        refresh_token="refresh",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    accounts.delete_account(household_id, "oda")
    assert accounts.get_account(household_id, "oda") is None


def test_delete_account_when_not_connected_is_a_noop(db: Session) -> None:
    accounts.delete_account("hh-nonexistent", "oda")  # should not raise


async def test_get_valid_access_token_returns_none_when_not_connected(db: Session) -> None:
    assert await accounts.get_valid_access_token("hh-1", "oda") is None


async def test_get_valid_access_token_returns_cached_token_without_refresh(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    household_id, user_id = _seed_household_and_user(db)
    accounts.upsert_account(
        household_id=household_id,
        provider="oda",
        connected_by_user_id=user_id,
        client_id="client-abc",
        client_secret="",
        access_token="still-valid-token",
        refresh_token="refresh-token",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )

    async def _fail_refresh(*args: object, **kwargs: object) -> TokenResponse:
        raise AssertionError("refresh should not be called for a non-expired token")

    monkeypatch.setattr("app.oda.oauth.refresh_access_token", _fail_refresh)

    token = await accounts.get_valid_access_token(household_id, "oda")
    assert token == "still-valid-token"


async def test_get_valid_access_token_refreshes_when_expired(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    household_id, user_id = _seed_household_and_user(db)
    accounts.upsert_account(
        household_id=household_id,
        provider="oda",
        connected_by_user_id=user_id,
        client_id="client-abc",
        client_secret="client-secret",
        access_token="expired-token",
        refresh_token="refresh-token",
        expires_at=datetime.now(timezone.utc) - timedelta(seconds=5),
    )

    async def _fake_discover(*args: object, **kwargs: object) -> object:
        return object()

    call_count = 0

    async def _fake_refresh(*args: object, **kwargs: object) -> TokenResponse:
        nonlocal call_count
        call_count += 1
        return TokenResponse(
            access_token="new-token",
            refresh_token="new-refresh-token",
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )

    monkeypatch.setattr("app.oda.oauth.discover_metadata", _fake_discover)
    monkeypatch.setattr("app.oda.oauth.refresh_access_token", _fake_refresh)

    token = await accounts.get_valid_access_token(household_id, "oda")
    assert token == "new-token"
    assert call_count == 1

    account = accounts.get_account(household_id, "oda")
    assert account is not None
    assert decrypt(account.access_token) == "new-token"
    assert decrypt(account.refresh_token) == "new-refresh-token"


async def test_get_valid_access_token_single_flight_under_concurrency(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two concurrent callers hitting an expired token must trigger exactly
    one refresh HTTP round-trip, not two racing refreshes."""
    household_id, user_id = _seed_household_and_user(db)
    accounts.upsert_account(
        household_id=household_id,
        provider="oda",
        connected_by_user_id=user_id,
        client_id="client-abc",
        client_secret="",
        access_token="expired-token",
        refresh_token="refresh-token",
        expires_at=datetime.now(timezone.utc) - timedelta(seconds=5),
    )

    async def _fake_discover(*args: object, **kwargs: object) -> object:
        return object()

    call_count = 0

    async def _fake_refresh(*args: object, **kwargs: object) -> TokenResponse:
        nonlocal call_count
        call_count += 1
        # Yield control so both concurrent callers are mid-refresh together
        # if the lock isn't actually serializing them.
        await asyncio.sleep(0.01)
        return TokenResponse(
            access_token="new-token",
            refresh_token="new-refresh-token",
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )

    monkeypatch.setattr("app.oda.oauth.discover_metadata", _fake_discover)
    monkeypatch.setattr("app.oda.oauth.refresh_access_token", _fake_refresh)

    results = await asyncio.gather(
        accounts.get_valid_access_token(household_id, "oda"),
        accounts.get_valid_access_token(household_id, "oda"),
    )

    assert results == ["new-token", "new-token"]
    assert call_count == 1


async def test_get_valid_access_token_returns_none_when_refresh_fails(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    household_id, user_id = _seed_household_and_user(db)
    accounts.upsert_account(
        household_id=household_id,
        provider="oda",
        connected_by_user_id=user_id,
        client_id="client-abc",
        client_secret="",
        access_token="expired-token",
        refresh_token="refresh-token",
        expires_at=datetime.now(timezone.utc) - timedelta(seconds=5),
    )

    async def _fake_discover(*args: object, **kwargs: object) -> object:
        return object()

    async def _fake_refresh_fails(*args: object, **kwargs: object) -> TokenResponse:
        raise OdaOAuthError("refresh failed")

    monkeypatch.setattr("app.oda.oauth.discover_metadata", _fake_discover)
    monkeypatch.setattr("app.oda.oauth.refresh_access_token", _fake_refresh_fails)

    token = await accounts.get_valid_access_token(household_id, "oda")
    assert token is None


async def test_get_valid_access_token_drops_account_on_decrypt_failure(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    household_id, user_id = _seed_household_and_user(db)
    accounts.upsert_account(
        household_id=household_id,
        provider="oda",
        connected_by_user_id=user_id,
        client_id="client-abc",
        client_secret="",
        access_token="still-valid-token",
        refresh_token="refresh-token",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )

    # Simulate app_secret_key rotation: decrypting with the "new" key fails.
    from app.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("APP_SECRET_KEY", "a-rotated-secret-key-value-here")
    get_settings.cache_clear()

    token = await accounts.get_valid_access_token(household_id, "oda")
    assert token is None
    assert accounts.get_account(household_id, "oda") is None
