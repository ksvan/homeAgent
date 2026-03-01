from __future__ import annotations

import fnmatch
import json
import logging
from dataclasses import dataclass

from sqlmodel import col, select

from app.db import users_session
from app.models.users import ActionPolicy

# Tool name prefixes that are clearly read-only — safe to allow when no policy matches.
_READ_PREFIXES = ("get_", "list_")

logger = logging.getLogger(__name__)


@dataclass
class PolicyDecision:
    requires_confirm: bool = False
    policy_name: str = ""
    confirm_message: str = ""
    impact_level: str = "low"


def evaluate_policy(tool_name: str, tool_args: dict[str, object]) -> PolicyDecision:
    """
    Evaluate whether a Homey tool call requires confirmation.

    tool_name: The MCP tool name WITHOUT the 'homey_' prefix
               (e.g. "set_device_capability", "get_device_state")
    tool_args: The arguments dict the agent passed to the tool.

    Returns a PolicyDecision describing what should happen.
    If no policy matches, the call is allowed to proceed (fail-open for
    read-only defaults; high-impact patterns should always be pre-seeded).
    """
    if not tool_name:
        return PolicyDecision()

    try:
        with users_session() as session:
            policies = session.exec(
                select(ActionPolicy)
                .where(ActionPolicy.enabled == True)  # noqa: E712
                # Confirmation-required policies first; then alphabetical by name.
                # This ensures specific high-impact rules always win over broader ones.
                .order_by(col(ActionPolicy.requires_confirm).desc(), col(ActionPolicy.name))
            ).all()
    except Exception:
        logger.warning("Policy lookup failed — defaulting to confirm", exc_info=True)
        return PolicyDecision(
            requires_confirm=True,
            policy_name="<policy-lookup-failed>",
            confirm_message=f"Execute '{tool_name}' on your Homey? (policy check unavailable)",
            impact_level="unknown",
        )

    for policy in policies:
        if not fnmatch.fnmatch(tool_name, policy.tool_pattern):
            continue

        # Check optional arg conditions (all must match)
        if policy.arg_conditions and policy.arg_conditions != "{}":
            try:
                conditions: dict[str, str] = json.loads(policy.arg_conditions)
                args_match = all(
                    fnmatch.fnmatch(str(tool_args.get(k, "")), v)
                    for k, v in conditions.items()
                )
                if not args_match:
                    continue
            except Exception:
                logger.warning("Malformed arg_conditions in policy '%s'", policy.name)
                continue

        return PolicyDecision(
            requires_confirm=policy.requires_confirm,
            policy_name=policy.name,
            confirm_message=policy.confirm_message
            or f"Execute '{tool_name}' on your Homey?",
            impact_level=policy.impact_level,
        )

    # No policy matched.
    # Read-only tools (get_*, list_*) are safe to execute without confirmation.
    # Any other unrecognised tool requires confirmation to be conservative.
    if tool_name.startswith(_READ_PREFIXES):
        return PolicyDecision()
    return PolicyDecision(
        requires_confirm=True,
        policy_name="<unmatched>",
        confirm_message=f"Execute '{tool_name}' on your Homey?",
        impact_level="unknown",
    )
