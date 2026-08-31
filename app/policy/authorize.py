"""
Single authoritative, live authorization decision point — see
docs/household-identity-and-access-design.md Option D.

Phase 0 checked account-level state only (is_active, is_admin for the admin
surface). Phase 2 adds the per-surface access flags (Goal 4) into this same
function without changing its signature or default-deny behavior — callers
wired up in Phase 0/1 don't need to change when this lands, since the new
fields default to True (today's access, unchanged) at every existing call
site that doesn't pass them explicitly.

Deliberately takes a plain `Principal`, not the `User` ORM model, so this
stays a pure function: no DB access, no import cycle risk, trivially
unit-testable with plain values. Callers load a `User` row and build a
`Principal` from it at the call site (see app.policy.principal.load_principal).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Surface = Literal["telegram", "web_chat", "admin"]


@dataclass(frozen=True)
class Principal:
    """The subset of a User's state authorize() needs to decide."""

    user_id: str
    is_active: bool
    is_admin: bool
    telegram_enabled: bool = True
    web_chat_enabled: bool = True


@dataclass(frozen=True)
class AuthDecision:
    allowed: bool
    reason: str


def authorize(principal: Principal | None, surface: Surface) -> AuthDecision:
    """Default-deny: every path that doesn't explicitly allow, denies."""
    if principal is None:
        return AuthDecision(False, "no_principal")
    if not principal.is_active:
        return AuthDecision(False, "account_disabled")
    if surface == "admin":
        if not principal.is_admin:
            return AuthDecision(False, "not_admin")
        return AuthDecision(True, "ok")
    if surface == "telegram" and not principal.telegram_enabled:
        return AuthDecision(False, "telegram_disabled")
    if surface == "web_chat" and not principal.web_chat_enabled:
        return AuthDecision(False, "web_chat_disabled")
    return AuthDecision(True, "ok")
