"""
Loads a `Principal` from `User` — the DB-touching half of
docs/household-identity-and-access-design.md Option D that authorize.py's
own docstring deliberately keeps out of that pure module.

Shared by every authorize() call site that starts from a `user_id`
(web chat's HTTP/WS auth, Telegram ingress) so there's exactly one place
that decides which `User` columns feed a `Principal`.
"""

from __future__ import annotations

from app.policy.authorize import Principal


def load_principal(user_id: str) -> Principal | None:
    # Deferred import (not at module level) so tests that monkeypatch
    # app.db.users_session still take effect regardless of import order —
    # every other DB-touching module in this codebase follows the same
    # pattern for the same reason.
    from sqlmodel import select

    from app.db import users_session
    from app.models.users import User

    with users_session() as db:
        user = db.exec(select(User).where(User.id == user_id)).first()
    if user is None:
        return None
    return Principal(
        user_id=user.id,
        is_active=user.is_active,
        is_admin=user.is_admin,
        telegram_enabled=user.telegram_enabled,
        web_chat_enabled=user.web_chat_enabled,
    )
