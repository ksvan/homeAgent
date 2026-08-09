from __future__ import annotations

import fnmatch
import json
import logging
from dataclasses import dataclass

from sqlmodel import col, select

from app.db import users_session
from app.models.users import ActionPolicy

# Tool name prefixes that are clearly read-only — safe to allow when no policy matches.
_READ_PREFIXES = ("get_", "list_", "search_")

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
                    fnmatch.fnmatch(str(tool_args.get(k, "")), v) for k, v in conditions.items()
                )
                if not args_match:
                    continue
            except Exception:
                logger.warning("Malformed arg_conditions in policy '%s'", policy.name)
                continue

        # For tools with a dynamic per-call message (Homey's use_tool meta-tool,
        # Oda's manipulate_cart), build it from the args. For everything else,
        # use the policy's configured static message.
        if tool_name in _DYNAMIC_CONFIRM_MESSAGE_TOOLS:
            msg = _build_confirm_message(tool_name, tool_args)
        else:
            msg = policy.confirm_message or f"Execute '{tool_name}' on your Homey?"

        return PolicyDecision(
            requires_confirm=policy.requires_confirm,
            policy_name=policy.name,
            confirm_message=msg,
            impact_level=policy.impact_level,
        )

    # No policy matched.
    # Read-only tools (get_*, list_*, search_*) are safe to execute without confirmation.
    # Any other unrecognised tool requires confirmation to be conservative.
    if tool_name.startswith(_READ_PREFIXES):
        return PolicyDecision()
    return PolicyDecision(
        requires_confirm=True,
        policy_name="<unmatched>",
        confirm_message=_build_confirm_message(tool_name, tool_args),
        impact_level="unknown",
    )


_DYNAMIC_CONFIRM_MESSAGE_TOOLS = frozenset({"use_tool", "manipulate_cart"})


def _build_confirm_message(tool_name: str, tool_args: dict[str, object]) -> str:
    """Build a human-readable confirmation message for the given tool call."""
    if tool_name == "use_tool":
        inner = str(tool_args.get("name", ""))
        if inner:
            return f"Execute Homey action '{inner}'?"
    if tool_name == "manipulate_cart":
        return _build_manipulate_cart_message(tool_args)
    return f"Execute '{tool_name}' on your Homey?"


def _build_manipulate_cart_message(tool_args: dict[str, object]) -> str:
    """Summarize an Oda manipulate_cart call by operation count/direction.

    Note: operations only ever carry a numeric productId, never a product
    name (confirmed against Oda's own tool schema) — there's no way to say
    "add Salami" here without an extra live lookup at confirm-build time,
    which this deliberately does not do (adds latency/complexity for a
    cosmetic improvement; the agent's own chat reply already names the
    product in practice, just not the confirmation card itself).
    """
    operations = tool_args.get("operations")
    if not isinstance(operations, list) or not operations:
        return "Update the shared Oda cart?"

    adds = removes = reorders = 0
    for op in operations:
        if not isinstance(op, dict):
            continue
        if op.get("orderNumber"):
            reorders += 1
            continue
        quantity = op.get("quantity") or 0
        if isinstance(quantity, (int, float)) and quantity < 0:
            removes += 1
        else:
            adds += 1

    parts: list[str] = []
    if adds:
        parts.append(f"add {adds} item{'s' if adds != 1 else ''}")
    if removes:
        parts.append(f"remove {removes} item{'s' if removes != 1 else ''}")
    if reorders:
        parts.append(f"re-add {reorders} previous order{'s' if reorders != 1 else ''}")

    if not parts:
        return "Update the shared Oda cart?"
    return "Update the shared Oda cart — " + ", ".join(parts) + "?"
