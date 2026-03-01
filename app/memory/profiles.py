from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from sqlmodel import select

from app.db import memory_session
from app.models.memory import HouseholdProfile, UserProfile

logger = logging.getLogger(__name__)


def get_user_profile(user_id: str) -> dict[str, object]:
    """Return the profile dict for a user (empty dict if none exists)."""
    with memory_session() as session:
        profile = session.exec(
            select(UserProfile).where(UserProfile.user_id == user_id)
        ).first()
        if profile is None:
            return {}
        return json.loads(profile.summary)  # type: ignore[no-any-return]


def upsert_user_profile(user_id: str, data: dict[str, object]) -> None:
    """Merge data into the user profile, creating it if absent."""
    with memory_session() as session:
        profile = session.exec(
            select(UserProfile).where(UserProfile.user_id == user_id)
        ).first()
        if profile is None:
            profile = UserProfile(user_id=user_id, summary=json.dumps(data))
            session.add(profile)
        else:
            existing: dict[str, object] = json.loads(profile.summary)
            existing.update(data)
            profile.summary = json.dumps(existing)
            profile.updated_at = datetime.now(timezone.utc)
        session.commit()


def get_household_profile(household_id: str) -> dict[str, object]:
    """Return the profile dict for a household (empty dict if none exists)."""
    with memory_session() as session:
        profile = session.exec(
            select(HouseholdProfile).where(HouseholdProfile.household_id == household_id)
        ).first()
        if profile is None:
            return {}
        return json.loads(profile.summary)  # type: ignore[no-any-return]


def upsert_household_profile(household_id: str, data: dict[str, object]) -> None:
    """Merge data into the household profile, creating it if absent."""
    with memory_session() as session:
        profile = session.exec(
            select(HouseholdProfile).where(HouseholdProfile.household_id == household_id)
        ).first()
        if profile is None:
            profile = HouseholdProfile(household_id=household_id, summary=json.dumps(data))
            session.add(profile)
        else:
            existing: dict[str, object] = json.loads(profile.summary)
            existing.update(data)
            profile.summary = json.dumps(existing)
            profile.updated_at = datetime.now(timezone.utc)
        session.commit()


def format_profile(profile: dict[str, object], label: str) -> str:
    """Format a profile dict as bullet-point text for the system prompt."""
    if not profile:
        return ""
    lines = [f"## {label}"]
    for key, value in profile.items():
        lines.append(f"- {key}: {value}")
    return "\n".join(lines)
