"""
Single authoritative, live authorization decision point — see
docs/household-identity-and-access-design.md Option D.

Phase 0 checks account-level state only (is_active, is_admin for the admin
surface). Phase 2 adds per-surface access flags into this same function
without changing its signature or default-deny behavior — callers wired up
in Phase 0/1 don't need to change when that lands.

Deliberately takes a plain `Principal`, not the `User` ORM model, so this
stays a pure function: no DB access, no import cycle risk, trivially
unit-testable with plain values. Callers load a `User` row and build a
`Principal` from it at the call site.
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
    if surface == "admin" and not principal.is_admin:
        return AuthDecision(False, "not_admin")
    return AuthDecision(True, "ok")
