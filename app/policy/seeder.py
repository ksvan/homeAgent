from __future__ import annotations

import logging

from sqlmodel import select

from app.db import users_session
from app.models.users import ActionPolicy
from app.policy.default_policies import DEFAULT_POLICIES

logger = logging.getLogger(__name__)


def seed_policies() -> None:
    """
    Insert missing default policies into the DB.

    Existing policies (by name) are never overwritten, so user edits survive
    application upgrades.
    """
    with users_session() as session:
        existing_names = {p.name for p in session.exec(select(ActionPolicy)).all()}

    missing = [p for p in DEFAULT_POLICIES if p["name"] not in existing_names]
    if not missing:
        logger.debug("All default policies already present — nothing to seed")
        return

    with users_session() as session:
        for policy_data in missing:
            session.add(ActionPolicy(**policy_data))
        session.commit()

    logger.info("Seeded %d default polic%s", len(missing), "y" if len(missing) == 1 else "ies")
