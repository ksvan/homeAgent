"""
One-time backfill: set member_id on existing personal EpisodicMemory rows.

Designed for the current single-user deployment where all existing personal
memories belong to Kristian. The script asserts that assumption before writing.

Run after both Alembic migrations are applied:
    uv run python -m app.memory.backfill_member_id

Safe to run multiple times — skips rows that already have member_id set.
"""
from __future__ import annotations

import logging
import sys

from sqlmodel import select

logger = logging.getLogger(__name__)


def run_backfill() -> None:
    from app.db import memory_session, users_session
    from app.models.memory import EpisodicMemory
    from app.models.users import User
    from app.models.world import HouseholdMember

    with users_session() as session:
        users = list(session.exec(select(User)).all())

    if not users:
        logger.error("Backfill aborted: no users found.")
        sys.exit(1)

    active_users = [u for u in users]
    if len(active_users) != 1:
        logger.error(
            "Backfill aborted: expected exactly 1 user but found %d. "
            "Review before running on a multi-user deployment.",
            len(active_users),
        )
        sys.exit(1)

    user = active_users[0]
    logger.info("Backfill: single user confirmed — %s (id=%s)", user.name, user.id[:8])

    with users_session() as session:
        member = session.exec(
            select(HouseholdMember).where(HouseholdMember.user_id == user.id)
        ).first()

    if not member:
        logger.error(
            "Backfill aborted: no HouseholdMember linked to user %s. "
            "Run /me name first to create the member link.",
            user.id[:8],
        )
        sys.exit(1)

    logger.info(
        "Backfill: member confirmed — %s (id=%s)", member.name, member.id[:8]
    )

    with memory_session() as session:
        rows = list(session.exec(
            select(EpisodicMemory).where(
                EpisodicMemory.user_id == user.id,
                EpisodicMemory.member_id.is_(None),  # type: ignore[union-attr]
            )
        ).all())

    if not rows:
        logger.info("Backfill: no rows to update — all personal memories already have member_id.")
        return

    logger.info("Backfill: updating %d personal memory rows...", len(rows))

    with memory_session() as session:
        updated = 0
        for row in rows:
            record = session.exec(
                select(EpisodicMemory).where(EpisodicMemory.id == row.id)
            ).first()
            if record and record.member_id is None:
                record.member_id = member.id
                session.add(record)
                updated += 1
        session.commit()

    logger.info("Backfill complete: %d rows updated.", updated)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    run_backfill()
