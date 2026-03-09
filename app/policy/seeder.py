from __future__ import annotations

import logging

from sqlmodel import select

from app.db import users_session
from app.models.users import ActionPolicy
from app.policy.default_policies import DEFAULT_POLICIES

logger = logging.getLogger(__name__)


def seed_policies() -> None:
    """
    Upsert default policies into the DB.

    Policies are matched by name. Existing records are updated to match the
    current defaults so that changes to default_policies.py take effect on
    the next startup without manual DB edits.
    """
    with users_session() as session:
        existing = {p.name: p for p in session.exec(select(ActionPolicy)).all()}

    inserted = updated = 0
    with users_session() as session:
        for policy_data in DEFAULT_POLICIES:
            name = str(policy_data["name"])
            if name in existing:
                p = existing[name]
                for k, v in policy_data.items():
                    setattr(p, k, v)
                session.add(p)
                updated += 1
            else:
                session.add(ActionPolicy(**policy_data))
                inserted += 1
        session.commit()

    if inserted or updated:
        logger.info("Policies: %d inserted, %d updated", inserted, updated)
    else:
        logger.debug("Policies already up to date — nothing to seed")
