"""Unit tests for app.memory.profiles and app.memory.pii.

Covers:
- PII detection (contains_pii) — pure regex logic, no mocking needed
- _filter_pii_from_dict — filters values that trigger the PII guard
- format_profile — pure formatting function
- upsert/get cycle using in-memory memory DB
"""
from __future__ import annotations

from contextlib import contextmanager

import pytest
from sqlmodel import Session

from app.memory.pii import contains_pii
from app.memory.profiles import (
    _filter_pii_from_dict,
    format_profile,
    get_household_profile,
    get_user_profile,
    upsert_household_profile,
    upsert_user_profile,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def profile_db(monkeypatch: pytest.MonkeyPatch, in_memory_engine: object) -> object:
    """Patch memory_session to use the in-memory engine."""

    @contextmanager
    def _session():  # type: ignore[misc]
        with Session(in_memory_engine) as s:  # type: ignore[arg-type]
            yield s

    monkeypatch.setattr("app.memory.profiles.memory_session", _session)
    return in_memory_engine


# ---------------------------------------------------------------------------
# contains_pii — pure regex
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "4111 1111 1111 1111",      # payment card
        "GB29NWBK60161331926819",   # IBAN
        "123-45-6789",              # US SSN
        "password: s3cr3t!",        # password literal
        "pin: 1234",                # PIN in context
        "192.168.1.1",              # IPv4
        "00:11:22:33:44:55",        # MAC address
    ],
)
def test_contains_pii_detects_sensitive_patterns(text: str) -> None:
    assert contains_pii(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "Likes football and piano",
        "Wakes up at 07:30",
        "Name is Alice",
        "Favourite colour: blue",
        "age: 32",
    ],
)
def test_contains_pii_allows_safe_text(text: str) -> None:
    assert contains_pii(text) is False


# ---------------------------------------------------------------------------
# _filter_pii_from_dict
# ---------------------------------------------------------------------------


def test_filter_pii_removes_pii_values() -> None:
    data = {
        "name": "Alice",
        "card": "4111 1111 1111 1111",
        "ip": "192.168.1.1",
    }
    result = _filter_pii_from_dict(data)
    assert "name" in result
    assert "card" not in result
    assert "ip" not in result


def test_filter_pii_keeps_all_safe_values() -> None:
    data = {"hobby": "piano", "age": "32", "city": "Oslo"}
    result = _filter_pii_from_dict(data)
    assert result == data


def test_filter_pii_empty_dict_returns_empty() -> None:
    assert _filter_pii_from_dict({}) == {}


def test_filter_pii_all_pii_returns_empty() -> None:
    data = {"ssn": "123-45-6789", "mac": "00:11:22:33:44:55"}
    assert _filter_pii_from_dict(data) == {}


# ---------------------------------------------------------------------------
# format_profile
# ---------------------------------------------------------------------------


def test_format_profile_empty_returns_empty_string() -> None:
    assert format_profile({}, "User") == ""


def test_format_profile_returns_section_header() -> None:
    result = format_profile({"hobby": "piano"}, "My Profile")
    assert result.startswith("## My Profile")


def test_format_profile_each_key_is_bullet() -> None:
    result = format_profile({"hobby": "piano", "city": "Oslo"}, "Profile")
    assert "- hobby: piano" in result
    assert "- city: Oslo" in result


# ---------------------------------------------------------------------------
# get_user_profile / upsert_user_profile
# ---------------------------------------------------------------------------


def test_get_user_profile_missing_returns_empty(profile_db: object) -> None:
    assert get_user_profile("nonexistent-user") == {}


def test_upsert_creates_profile(profile_db: object) -> None:
    upsert_user_profile("u-1", {"hobby": "chess"})
    result = get_user_profile("u-1")
    assert result["hobby"] == "chess"


def test_upsert_merges_keys(profile_db: object) -> None:
    upsert_user_profile("u-2", {"hobby": "chess"})
    upsert_user_profile("u-2", {"city": "Bergen"})
    result = get_user_profile("u-2")
    assert result["hobby"] == "chess"
    assert result["city"] == "Bergen"


def test_upsert_overwrites_existing_key(profile_db: object) -> None:
    upsert_user_profile("u-3", {"hobby": "chess"})
    upsert_user_profile("u-3", {"hobby": "piano"})
    result = get_user_profile("u-3")
    assert result["hobby"] == "piano"


def test_upsert_pii_only_data_is_no_op(profile_db: object) -> None:
    upsert_user_profile("u-4", {"card": "4111 1111 1111 1111"})
    assert get_user_profile("u-4") == {}


def test_upsert_mixed_data_stores_only_safe_keys(profile_db: object) -> None:
    upsert_user_profile("u-5", {"hobby": "chess", "ssn": "123-45-6789"})
    result = get_user_profile("u-5")
    assert "hobby" in result
    assert "ssn" not in result


# ---------------------------------------------------------------------------
# get_household_profile / upsert_household_profile
# ---------------------------------------------------------------------------


def test_get_household_profile_missing_returns_empty(profile_db: object) -> None:
    assert get_household_profile("hh-none") == {}


def test_upsert_household_creates_and_merges(profile_db: object) -> None:
    upsert_household_profile("hh-1", {"language": "no"})
    upsert_household_profile("hh-1", {"pets": "dog"})
    result = get_household_profile("hh-1")
    assert result["language"] == "no"
    assert result["pets"] == "dog"
