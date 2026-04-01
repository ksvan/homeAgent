"""Build structured prompt envelopes for proactive scheduled runs."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.models.scheduled_prompts import ScheduledPrompt, ScheduledPromptLink

logger = logging.getLogger(__name__)

# Map entity_type → (model class, name attribute) for resolving IDs to display names.
_ENTITY_RESOLVERS: dict[str, tuple[str, str]] = {
    "member": ("app.models.world.HouseholdMember", "name"),
    "place": ("app.models.world.Place", "name"),
    "device": ("app.models.world.DeviceEntity", "name"),
    "calendar": ("app.models.world.CalendarEntity", "name"),
    "routine": ("app.models.world.RoutineEntity", "name"),
}


def _resolve_entity_name(entity_type: str, entity_id: str) -> str:
    """Resolve an entity ID to a display name. Returns the ID if not found."""
    spec = _ENTITY_RESOLVERS.get(entity_type)
    if not spec:
        return entity_id

    module_path, attr = spec
    try:
        import importlib

        parts = module_path.rsplit(".", 1)
        mod = importlib.import_module(parts[0])
        model_cls = getattr(mod, parts[1])

        from app.db import users_session

        with users_session() as session:
            entity = session.get(model_cls, entity_id)
            if entity:
                return getattr(entity, attr, entity_id)
    except Exception:
        logger.debug("Could not resolve %s/%s", entity_type, entity_id, exc_info=True)

    return entity_id


def build_prompt_envelope(
    sp: ScheduledPrompt,
    links: list[ScheduledPromptLink] | None = None,
) -> str:
    """Return the text passed to ``run_conversation()`` for a proactive run.

    For ``generic_prompt`` (or NULL kind): a minimal header + raw prompt.
    For structured kinds: a full envelope with kind, goal, schedule, delivery hints,
    and linked entities.
    """
    from app.scheduler.delivery import parse_delivery_policy
    from app.scheduler.scheduled_prompts import recurrence_label

    kind = sp.behavior_kind
    schedule = recurrence_label(sp.recurrence, sp.time_of_day, sp.run_at)

    if not kind or kind == "generic_prompt":
        header = f'[Scheduled prompt: "{sp.name}" — {schedule}]'
        if links:
            entity_parts = _format_linked_entities(links)
            if entity_parts:
                header += f"\n[Linked: {entity_parts}]"
        return f"{header}\n{sp.prompt}"

    # Structured envelope
    policy = parse_delivery_policy(sp)
    policy_hints = []
    if policy.get("skip_if_empty"):
        policy_hints.append("skip if empty")
    if policy.get("skip_if_unchanged"):
        policy_hints.append("skip if unchanged")
    policy_str = ", ".join(policy_hints) if policy_hints else "deliver always"

    lines = [
        "## Proactive Scheduled Run",
        f"- **Kind**: {kind}",
    ]
    if sp.goal:
        lines.append(f"- **Goal**: {sp.goal}")
    lines.append(f"- **Schedule**: {schedule}")
    lines.append(f"- **Delivery policy**: {policy_str}")

    if links:
        lines.append("")
        lines.append("## Linked Entities")
        for link in links:
            name = _resolve_entity_name(link.entity_type, link.entity_id)
            lines.append(f"- {link.entity_type}: {name} ({link.role})")

    lines.append("")
    lines.append("## Prompt")
    lines.append(sp.prompt)

    return "\n".join(lines)


def _format_linked_entities(links: list[ScheduledPromptLink]) -> str:
    """Compact inline format for the generic_prompt header."""
    parts = []
    for link in links:
        name = _resolve_entity_name(link.entity_type, link.entity_id)
        parts.append(f"{link.entity_type}: {name}")
    return ", ".join(parts)
